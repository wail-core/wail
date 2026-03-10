"""
wail.testing — first-class pytest harness for WAIL plugins.

Auto-registered as a pytest plugin when wail-core is installed, so fixtures are
available in any test file with no imports or conftest.py boilerplate needed.

Quick-start
-----------
Write a plugin, then test it::

    # my_plugin.py
    from integrations.base import AbstractIntegration

    class CrmIntegration(AbstractIntegration):
        name         = "crm"
        display_name = "CRM"
        description  = "Push contacts to HubSpot after each booking."

        def get_mcp_tools(self, wail_base, api_key):
            def search_contacts(query: str) -> dict:
                "Search contacts by name."
                import httpx
                return httpx.get(f"https://api.hubspot.com/contacts?q={query}").json()
            return [search_contacts]

    # test_my_plugin.py
    from my_plugin import CrmIntegration

    def test_contract(wail_test_client):
        validate_plugin(CrmIntegration)

    def test_manifest(wail_test_client):
        r = wail_test_client.get("/integrations/manifest")
        assert r.status_code == 200
        names = [i["name"] for i in r.json()]
        assert "crm" in names

    def test_appointments(wail_test_client):
        r = wail_test_client.simulate_agent_request("What appointments are available?")
        assert r.status_code == 200

    def test_mcp_tool(wail_test_client):
        result = wail_test_client.call_mcp_tool("crm", "search_contacts", query="Alice")
        assert "contacts" in result or "error" in result  # result is a dict

Fixtures provided
-----------------
  wail_test_client   WailTestClient pre-wired with mock calendar + payment connections.
                     Pass ``plugins=[MyIntegration()]`` to add plugin integrations.
  mock_calendar      A standalone MockCalendarConnection instance.
  mock_payment       A standalone MockPaymentConnection instance.

Utilities
---------
  validate_plugin(cls_or_instance)   Assert the AbstractIntegration contract.
  fake_event(**overrides)            Build a RawCalendarEvent for test data.
  fake_booking_config(**overrides)   Build a minimal booking config dict.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import pytest


# ── Mock: CalendarConnection ─────────────────────────────────────────────────

class MockCalendarConnection:
    """
    In-memory calendar connection — satisfies AbstractCalendarConnection.

    Pre-loaded with a small set of realistic fake events so that booking
    slot availability, event listing, and group tests all have data to work
    with out of the box.

    Recorded writes (create_event / delete_event) are stored on:
        mock_calendar.created_events   list[NewCalendarEvent]
        mock_calendar.deleted_events   list[str]   (event IDs)

    Override the initial event set::

        mock = MockCalendarConnection(events=[fake_event(title="Team sync")])

    Or add events after construction::

        mock_calendar.events.append(fake_event(title="Lunch"))
    """

    name:         str = "mock_calendar"
    display_name: str = "Mock Calendar"

    def __init__(self, events=None, calendars=None) -> None:
        from connections.base import CalendarInfo

        today_str  = date.today().isoformat()
        self.events: list = events if events is not None else [
            fake_event(
                id="evt-existing-1",
                title="Existing appointment",
                start=f"{today_str}T09:00:00",
                end=f"{today_str}T09:30:00",
                description="WAIL-Booking: true\nWAIL-Contact: alice@example.com",
            ),
            fake_event(
                id="evt-existing-2",
                title="Existing appointment",
                start=f"{today_str}T14:00:00",
                end=f"{today_str}T14:30:00",
                description="WAIL-Booking: true\nWAIL-Contact: bob@example.com",
            ),
        ]
        self.calendars: list = calendars or [
            CalendarInfo(id="primary", name="Primary Calendar", primary=True)
        ]
        self.created_events: list = []
        self.deleted_events: list[str] = []
        self._next_id = 1

    # ── AbstractConnection contract ───────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    # ── AbstractCalendarConnection contract ───────────────────────────────────

    def fetch_events(self, calendar_id, time_min, time_max=None, max_results=200):
        """Return stored events filtered to [time_min, time_max]."""
        result = []
        for ev in self.events:
            ev_start = _parse_dt(ev.start)
            if ev_start is None:
                continue
            if ev_start < time_min:
                continue
            if time_max is not None and ev_start > time_max:
                continue
            result.append(ev)
        return result[:max_results]

    def get_event(self, calendar_id, event_id):
        for ev in self.events:
            if ev.id == event_id:
                return ev
        from connections.base import RawCalendarEvent
        raise RuntimeError(f"Mock: event '{event_id}' not found")

    def create_event(self, calendar_id, event):
        """Record the event and return a synthetic ID."""
        self.created_events.append(event)
        event_id = f"mock-event-{self._next_id}"
        self._next_id += 1
        # Also add a fake RawCalendarEvent so subsequent fetch_events can see it
        from connections.base import RawCalendarEvent
        self.events.append(RawCalendarEvent(
            id=event_id,
            title=event.title,
            start=event.start_dt.isoformat(),
            end=event.end_dt.isoformat(),
            description=event.description,
        ))
        return event_id

    def delete_event(self, calendar_id, event_id):
        """Record the deletion and remove from the in-memory list."""
        self.deleted_events.append(event_id)
        self.events = [e for e in self.events if e.id != event_id]

    def list_calendars(self):
        return self.calendars

    # ── Test assertions ───────────────────────────────────────────────────────

    def assert_event_created(self, *, title=None, count=None) -> None:
        """
        Assert that at least one event was created via this connection.

        Args:
            title: If given, assert that a created event has this title substring.
            count: If given, assert that exactly this many events were created.

        Raises:
            AssertionError on failure.
        """
        if count is not None:
            assert len(self.created_events) == count, (
                f"Expected {count} created event(s), got {len(self.created_events)}"
            )
        else:
            assert self.created_events, "No events were created"

        if title:
            titles = [e.title for e in self.created_events]
            assert any(title in t for t in titles), (
                f"No created event with title containing {title!r}. Titles: {titles}"
            )

    def assert_event_deleted(self, event_id: str | None = None) -> None:
        """
        Assert that at least one event was deleted.

        Args:
            event_id: If given, assert this specific event ID was deleted.
        """
        assert self.deleted_events, "No events were deleted"
        if event_id:
            assert event_id in self.deleted_events, (
                f"Event '{event_id}' was not deleted. Deleted: {self.deleted_events}"
            )


# ── Mock: PaymentConnection ───────────────────────────────────────────────────

class MockPaymentConnection:
    """
    In-memory payment connection — satisfies AbstractPaymentConnection.

    All sessions are immediately in "complete" / "paid" state so that
    post-payment action dispatch can be tested without a real provider.

    Recorded calls::

        mock_payment.created_sessions   list[dict]   (kwargs passed to create_checkout_session)
        mock_payment.webhook_events     list[dict]   (events passed through verify_webhook)

    Simulate a failed payment::

        mock_payment.session_status = "expired"
    """

    name:         str = "mock_payment"
    display_name: str = "Mock Payment"

    def __init__(self) -> None:
        self.session_status:  str  = "complete"
        self.payment_status:  str  = "paid"
        self.created_sessions: list = []
        self.webhook_events:   list = []
        self._next_session    = 1

    # ── AbstractConnection contract ───────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def get_webhook_secret(self) -> str:
        return "whsec_mock"

    # ── AbstractPaymentConnection contract ────────────────────────────────────

    def create_checkout_session(
        self,
        amount_cents,
        currency,
        description,
        metadata,
        success_url,
        cancel_url,
        customer_email="",
        customer_name="",
    ):
        from connections.payment import CheckoutSession

        session_id = f"mock_sess_{self._next_session}"
        self._next_session += 1
        self.created_sessions.append({
            "session_id":     session_id,
            "amount_cents":   amount_cents,
            "currency":       currency,
            "description":    description,
            "metadata":       metadata,
            "customer_email": customer_email,
        })
        return CheckoutSession(
            session_id=session_id,
            payment_url=f"https://mock-checkout.example.com/pay/{session_id}",
        )

    def get_session_status(self, session_id):
        from connections.payment import SessionStatus

        return SessionStatus(
            session_id=session_id,
            status=self.session_status,
            payment_status=self.payment_status,
            metadata={},
        )

    def verify_webhook(self, payload_bytes, signature, secret):
        """Accept any payload in test mode (no signature verification)."""
        import json
        event = json.loads(payload_bytes)
        self.webhook_events.append(event)
        return event

    # ── Test assertions ───────────────────────────────────────────────────────

    def assert_session_created(self, *, count=None, min_amount_cents=None) -> None:
        """Assert that at least one checkout session was created."""
        if count is not None:
            assert len(self.created_sessions) == count, (
                f"Expected {count} session(s), got {len(self.created_sessions)}"
            )
        else:
            assert self.created_sessions, "No checkout sessions were created"

        if min_amount_cents is not None:
            amounts = [s["amount_cents"] for s in self.created_sessions]
            assert any(a >= min_amount_cents for a in amounts), (
                f"No session with amount >= {min_amount_cents} cents. Amounts: {amounts}"
            )


# ── Fake data factories ───────────────────────────────────────────────────────

def fake_event(**overrides) -> Any:
    """
    Build a RawCalendarEvent populated with sensible defaults.

    All fields can be overridden::

        ev = fake_event(title="Team sync", start="2026-03-10T10:00:00")

    Returns a RawCalendarEvent Pydantic model.
    """
    from connections.base import RawCalendarEvent

    today = date.today().isoformat()
    defaults = {
        "id":             "evt-fake-001",
        "title":          "Test appointment",
        "start":          f"{today}T10:00:00",
        "end":            f"{today}T10:30:00",
        "description":    "",
        "location":       "",
        "status":         "confirmed",
        "attendee_count": 0,
    }
    defaults.update(overrides)
    return RawCalendarEvent(**defaults)


def fake_booking_config(**overrides) -> dict:
    """
    Build a minimal valid booking config dict.

    Merges overrides on top of the defaults so you only need to specify
    what's relevant to your test::

        config = fake_booking_config(
            timezone="America/Chicago",
            services=[{"id": "consult", "name": "Consultation",
                       "duration_minutes": 60, "price": 150}],
        )
    """
    from integrations.booking import DEFAULT_CONFIG
    result = dict(DEFAULT_CONFIG)
    result.update(overrides)
    return result


# ── Plugin contract validator ─────────────────────────────────────────────────

def validate_plugin(cls_or_instance) -> None:
    """
    Assert that an AbstractIntegration subclass satisfies the full contract.

    Raises ``AssertionError`` with a descriptive message on the first
    violation found.  Raises ``TypeError`` if the class cannot be
    instantiated without arguments (create an instance yourself and pass it).

    Checks:
    - ``name``, ``display_name``, ``description`` class variables are set.
    - ``get_settings_schema()`` returns a dict with a ``"type"`` key.
    - ``get_mcp_tools("http://localhost", "")`` returns a list of callables.
    - Every MCP tool has a non-empty docstring (required by FastMCP).
    - Every MCP tool's parameter annotations are JSON-serialisable types.
    - ``get_manifest_extras()`` returns a dict.
    - ``is_enabled()`` returns a bool.
    - ``set_enabled(True)`` does not raise.

    Example::

        from my_plugin import CrmIntegration

        def test_contract():
            validate_plugin(CrmIntegration)
    """
    from integrations.base import AbstractIntegration
    import inspect as _inspect

    # Allow passing either a class or an instance
    if isinstance(cls_or_instance, type):
        cls = cls_or_instance
        try:
            instance = cls()
        except TypeError as e:
            raise TypeError(
                f"{cls.__name__} requires constructor arguments. "
                "Pass an instance instead of the class: validate_plugin(MyCls(arg1, arg2))"
            ) from e
    else:
        instance = cls_or_instance
        cls = type(instance)

    assert issubclass(cls, AbstractIntegration), (
        f"{cls.__name__} must subclass AbstractIntegration"
    )

    # Class variables
    for attr in ("name", "display_name", "description"):
        assert hasattr(cls, attr) and getattr(cls, attr), (
            f"{cls.__name__} must define a non-empty class variable '{attr}'"
        )
    assert isinstance(cls.name, str) and cls.name.replace("_", "").isalnum(), (
        f"{cls.__name__}.name must be an alphanumeric slug (underscores allowed), "
        f"got {cls.name!r}"
    )

    # Settings schema
    schema = instance.get_settings_schema()
    assert isinstance(schema, dict), (
        f"{cls.__name__}.get_settings_schema() must return a dict, got {type(schema)}"
    )
    assert "type" in schema, (
        f"{cls.__name__}.get_settings_schema() must have a 'type' key (e.g. 'object')"
    )

    # MCP tools
    tools = instance.get_mcp_tools("http://localhost:8000", "")
    assert isinstance(tools, list), (
        f"{cls.__name__}.get_mcp_tools() must return a list"
    )
    _JSON_TYPES = {str, int, float, bool, dict, list, type(None)}
    for fn in tools:
        assert callable(fn), (
            f"{cls.__name__}.get_mcp_tools() returned a non-callable: {fn!r}"
        )
        assert fn.__doc__ and fn.__doc__.strip(), (
            f"MCP tool '{fn.__name__}' in {cls.__name__} must have a docstring "
            "(FastMCP exposes the docstring to the agent as the tool description)"
        )
        sig = _inspect.signature(fn)
        for param_name, param in sig.parameters.items():
            ann = param.annotation
            if ann is _inspect.Parameter.empty:
                raise AssertionError(
                    f"MCP tool '{fn.__name__}' parameter '{param_name}' in "
                    f"{cls.__name__} is missing a type annotation — "
                    "FastMCP requires annotated parameters"
                )
            origin = getattr(ann, "__origin__", ann)
            assert origin in _JSON_TYPES, (
                f"MCP tool '{fn.__name__}' parameter '{param_name}' has type "
                f"{ann} which is not JSON-serialisable. "
                "Use str, int, float, bool, dict, or list."
            )

    # get_manifest_extras
    extras = instance.get_manifest_extras()
    assert isinstance(extras, dict), (
        f"{cls.__name__}.get_manifest_extras() must return a dict"
    )

    # Enable / disable
    enabled = instance.is_enabled()
    assert isinstance(enabled, bool), (
        f"{cls.__name__}.is_enabled() must return a bool, got {type(enabled)}"
    )
    instance.set_enabled(True)  # must not raise


# ── Routing helper for simulate_agent_request ─────────────────────────────────

_ROUTE_MAP = [
    # (keywords, method, path, query_params_factory)
    ({"slot", "available", "availability", "open"},
     "GET", "/integrations/google-calendar/booking/slots",
     lambda: {"date": date.today().isoformat(), "service_id": "haircut"}),
    ({"appointment", "booking", "service", "price", "book"},
     "GET", "/integrations/google-calendar/booking/config",
     lambda: {}),
    ({"cancel", "cancell"},
     "GET", "/integrations/google-calendar/booking/config",
     lambda: {}),
    ({"calendar", "event", "schedule"},
     "GET", "/integrations/google-calendar/events",
     lambda: {"date": date.today().isoformat()}),
    ({"payment", "pay", "checkout", "stripe"},
     "GET", "/integrations/payment/config",
     lambda: {}),
    ({"integration", "plugin", "manifest"},
     "GET", "/integrations/manifest",
     lambda: {}),
]


def _route_query(query: str) -> tuple[str, str, dict]:
    """Return (method, path, params) for the best-matching route."""
    words = set(query.lower().split())
    for keywords, method, path, params_fn in _ROUTE_MAP:
        if keywords & words:
            return method, path, params_fn()
    return "GET", "/", {}


# ── Patcher context manager ───────────────────────────────────────────────────

@contextmanager
def _patch_registry(wail_app) -> Iterator[None]:
    """
    Temporarily replace module-level singletons in integrations.registry and
    every router module that imported them, then restore on exit.

    This is necessary because the routers bind names at import time:
        from integrations.registry import booking_integration
    After the import, patching integrations.registry.booking_integration alone
    wouldn't affect the routers' local references.
    """
    import integrations.registry as _reg
    import routers.auth     as _auth_r
    import routers.booking  as _booking_r
    import routers.calendar as _calendar_r
    import routers.payment  as _payment_r
    import routers.config   as _config_r

    # Snapshot originals
    originals: dict[tuple, Any] = {}

    def _snap(module, attr):
        originals[(id(module), attr)] = getattr(module, attr, _MISSING)

    _MISSING = object()

    # integrations.registry (affects deps.py lazy imports)
    for attr in ("google_calendar_conn", "stripe_payment_conn",
                 "calendar_integration", "booking_integration",
                 "payment_integration", "ALL_INTEGRATIONS"):
        _snap(_reg, attr)

    # router modules
    for attr in ("google_calendar_conn",):
        _snap(_auth_r, attr)
    for attr in ("google_calendar_conn", "booking_integration",
                 "stripe_payment_conn", "payment_integration"):
        _snap(_booking_r, attr)
    for attr in ("google_calendar_conn", "calendar_integration"):
        _snap(_calendar_r, attr)
    for attr in ("stripe_payment_conn", "payment_integration"):
        _snap(_payment_r, attr)
    for attr in ("ALL_INTEGRATIONS",):
        _snap(_config_r, attr)

    # Apply patches
    _reg.google_calendar_conn  = wail_app.google_calendar_conn
    _reg.stripe_payment_conn   = wail_app.stripe_payment_conn
    _reg.calendar_integration  = wail_app.calendar_integration
    _reg.booking_integration   = wail_app.booking_integration
    _reg.payment_integration   = wail_app.payment_integration
    _reg.ALL_INTEGRATIONS      = wail_app.ALL_INTEGRATIONS

    _auth_r.google_calendar_conn    = wail_app.google_calendar_conn

    _booking_r.google_calendar_conn = wail_app.google_calendar_conn
    _booking_r.booking_integration  = wail_app.booking_integration
    _booking_r.stripe_payment_conn  = wail_app.stripe_payment_conn
    _booking_r.payment_integration  = wail_app.payment_integration

    _calendar_r.google_calendar_conn = wail_app.google_calendar_conn
    _calendar_r.calendar_integration = wail_app.calendar_integration

    _payment_r.stripe_payment_conn  = wail_app.stripe_payment_conn
    _payment_r.payment_integration  = wail_app.payment_integration

    _config_r.ALL_INTEGRATIONS      = wail_app.ALL_INTEGRATIONS

    try:
        yield
    finally:
        # Restore
        for (module_id, attr), original in originals.items():
            for mod in (_reg, _auth_r, _booking_r, _calendar_r,
                        _payment_r, _config_r):
                if id(mod) == module_id:
                    if original is _MISSING:
                        try:
                            delattr(mod, attr)
                        except AttributeError:
                            pass
                    else:
                        setattr(mod, attr, original)
                    break


# ── WailTestClient ────────────────────────────────────────────────────────────

class WailTestClient:
    """
    HTTP test client for WAIL — pre-wired with mock connections.

    Instantiate directly or use the ``wail_test_client`` pytest fixture.

    Args:
        plugins:       AbstractIntegration instances to inject as extra integrations.
                       Their MCP tools, settings schema, and manifest extras will
                       be visible in all test requests.
        calendar_conn: Override the default MockCalendarConnection.
        payment_conn:  Override the default MockPaymentConnection.

    As a context manager::

        with WailTestClient(plugins=[CrmIntegration()]) as client:
            r = client.get("/integrations/manifest")

    With the pytest fixture::

        def test_my_plugin(wail_test_client):
            r = wail_test_client.simulate_agent_request("What appointments do I have?")
            assert r.status_code == 200
    """

    def __init__(
        self,
        plugins:       list | None               = None,
        calendar_conn: MockCalendarConnection | None = None,
        payment_conn:  MockPaymentConnection  | None = None,
    ) -> None:
        from integrations.registry import WailApp

        self.mock_calendar = calendar_conn or MockCalendarConnection()
        self.mock_payment  = payment_conn  or MockPaymentConnection()
        self._wail_app     = WailApp(
            calendar_conn=self.mock_calendar,
            payment_conn=self.mock_payment,
            extra_integrations=plugins or [],
            discover_plugins=False,   # installed plugins must not bleed into unit tests
        )
        self._http_client  = None  # created in __enter__
        self._patch_ctx    = None

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "WailTestClient":
        from starlette.testclient import TestClient
        from wail import WAIL
        from routers.auth     import router as auth_router
        from routers.calendar import router as calendar_router
        from routers.booking  import router as booking_router
        from routers.trust    import router as trust_router
        from routers.payment  import router as payment_router
        from routers.config   import router as config_router
        from routers.plugins  import router as plugins_router

        self._patch_ctx = _patch_registry(self._wail_app)
        self._patch_ctx.__enter__()

        wail_obj = WAIL()
        app      = wail_obj.app
        app.include_router(auth_router)
        app.include_router(config_router)
        app.include_router(plugins_router)
        app.include_router(calendar_router)
        app.include_router(booking_router)
        app.include_router(trust_router)
        app.include_router(payment_router)

        self._http_client = TestClient(app, raise_server_exceptions=False)
        return self

    def __exit__(self, *args) -> None:
        if self._patch_ctx:
            self._patch_ctx.__exit__(*args)

    def _ensure_open(self) -> None:
        if self._http_client is None:
            raise RuntimeError(
                "WailTestClient must be used as a context manager or via the "
                "wail_test_client fixture."
            )

    # ── HTTP methods ──────────────────────────────────────────────────────────

    def get(self, path: str, **kwargs):
        self._ensure_open()
        return self._http_client.get(path, **kwargs)

    def post(self, path: str, **kwargs):
        self._ensure_open()
        return self._http_client.post(path, **kwargs)

    def delete(self, path: str, **kwargs):
        self._ensure_open()
        return self._http_client.delete(path, **kwargs)

    def patch(self, path: str, **kwargs):
        self._ensure_open()
        return self._http_client.patch(path, **kwargs)

    def put(self, path: str, **kwargs):
        self._ensure_open()
        return self._http_client.put(path, **kwargs)

    # ── High-level helpers ────────────────────────────────────────────────────

    def simulate_agent_request(self, query: str):
        """
        Route a natural-language query to the most appropriate WAIL endpoint.

        Keywords in *query* are matched against known WAIL route intents.
        Returns the HTTP response from that route.

        Keyword routing table:
            slot / available / open / availability → GET /booking/slots (today)
            appointment / booking / service / book → GET /booking/config
            calendar / event / schedule            → GET /calendar/events (today)
            payment / pay / checkout / stripe      → GET /payment/config
            integration / plugin / manifest        → GET /integrations/manifest
            (anything else)                        → GET / (WAIL manifest)

        Example::

            r = wail_test_client.simulate_agent_request("What slots are available today?")
            assert r.status_code == 200
            assert "slots" in r.json()
        """
        self._ensure_open()
        method, path, params = _route_query(query)
        if method == "GET":
            return self._http_client.get(path, params=params)
        return self._http_client.request(method, path, params=params)

    def call_mcp_tool(self, integration_name: str, tool_name: str, **kwargs) -> Any:
        """
        Call an MCP tool function in-process and return its result.

        Finds the tool by iterating ``integration.get_mcp_tools()`` — no HTTP
        involved, so the call is fast and the result can be inspected directly.

        Args:
            integration_name: The integration's ``name`` slug, e.g. ``"crm"``.
            tool_name:        The tool function's ``__name__``, e.g. ``"search_contacts"``.
            **kwargs:         Arguments forwarded to the tool function.

        Returns:
            Whatever the tool function returns (usually a dict).

        Raises:
            KeyError:  Integration or tool not found.
            Exception: Whatever the tool itself raises.

        Example::

            result = wail_test_client.call_mcp_tool("crm", "search_contacts", query="Alice")
            assert "contacts" in result
        """
        self._ensure_open()
        integration = next(
            (i for i in self._wail_app.ALL_INTEGRATIONS
             if i.name == integration_name),
            None,
        )
        if integration is None:
            raise KeyError(
                f"No integration named '{integration_name}'. "
                f"Available: {[i.name for i in self._wail_app.ALL_INTEGRATIONS]}"
            )
        tools = {fn.__name__: fn
                 for fn in integration.get_mcp_tools("http://localhost:7000", "")}
        if tool_name not in tools:
            raise KeyError(
                f"No tool '{tool_name}' in '{integration_name}'. "
                f"Available: {list(tools)}"
            )
        return tools[tool_name](**kwargs)

    def simulate_payment_webhook(
        self,
        action:       str,
        payload:      dict,
        amount_cents: int   = 0,
        session_id:   str   = "mock_sess_test",
    ) -> dict:
        """
        Fire a fake payment-complete webhook through the full dispatch path.

        Calls ``POST /integrations/payment/webhook`` with a synthetic Stripe
        event body. The mock payment connection accepts it without signature
        verification.

        Args:
            action:       Post-payment action registered in WailApp, e.g.
                          ``"create_booking"``.
            payload:      The payload dict stored in session metadata.
            amount_cents: Amount billed (for the synthetic event body).
            session_id:   Synthetic session ID (must not clash with real ones).

        Returns:
            The parsed JSON response from the webhook endpoint.

        Example::

            result = wail_test_client.simulate_payment_webhook(
                action="create_booking",
                payload={"date": "2026-03-15", "time": "10:00",
                         "service_id": "haircut", "name": "Alice",
                         "email": "alice@example.com"},
            )
            assert result.get("status") == "ok"
            mock_calendar.assert_event_created(title="haircut")
        """
        import json as _json

        meta_payload = _json.dumps(payload)
        body = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id":             session_id,
                    "amount_total":   amount_cents,
                    "metadata":       {"action": action, "payload": meta_payload},
                    "customer_email": payload.get("email", ""),
                    "customer_details": {"name": payload.get("name", "")},
                }
            },
        }
        raw_body = _json.dumps(body).encode()
        r = self._http_client.post(
            "/integrations/payment/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": "mock_sig",
            },
        )
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_dt(value: str) -> datetime | None:
    """Parse an ISO 8601 date or datetime string into a timezone-aware datetime."""
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except Exception:
        return None


# ── pytest fixtures (auto-registered via pytest11 entry point) ────────────────

@pytest.fixture
def mock_calendar() -> MockCalendarConnection:
    """
    A standalone MockCalendarConnection pre-loaded with two fake appointments.

    Inject this into tests that need direct access to the calendar mock —
    for example, to call ``mock_calendar.assert_event_created()``::

        def test_booking_creates_event(wail_test_client, mock_calendar):
            wail_test_client.post("/integrations/google-calendar/booking/book", json={...})
            mock_calendar.assert_event_created()
    """
    return MockCalendarConnection()


@pytest.fixture
def mock_payment() -> MockPaymentConnection:
    """
    A standalone MockPaymentConnection whose sessions always succeed.

    To test payment failure paths, set ``mock_payment.session_status = "expired"``
    before the call::

        def test_expired_payment(mock_payment):
            mock_payment.session_status = "expired"
            ...
    """
    return MockPaymentConnection()


@pytest.fixture
def wail_test_client(mock_calendar, mock_payment):
    """
    A WailTestClient connected to the mock calendar and payment connections.

    The full WAIL router stack is mounted and backed by the mocks, so HTTP
    requests behave exactly like production — they just don't hit real APIs.

    To add plugin integrations::

        @pytest.fixture
        def wail_test_client(mock_calendar, mock_payment):
            with WailTestClient(
                plugins=[CrmIntegration()],
                calendar_conn=mock_calendar,
                payment_conn=mock_payment,
            ) as client:
                yield client

    Default usage (no plugins)::

        def test_manifest(wail_test_client):
            r = wail_test_client.get("/integrations/manifest")
            assert r.status_code == 200
    """
    with WailTestClient(
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        yield client
