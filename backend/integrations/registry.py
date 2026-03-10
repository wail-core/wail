"""
WailApp — central application object that wires connections and integrations.

Usage (how main.py and routers use it):

    from integrations.registry import wail
    # or use the backward-compatible module-level names:
    from integrations.registry import google_calendar_conn, booking_integration, ...

Custom connection (e.g. swap Stripe for Paddle):

    from integrations.registry import WailApp
    from connections.my_paddle import PaddlePaymentConnection

    wail = WailApp(payment_conn=PaddlePaymentConnection())

Dependency graph:

    GoogleCalendarConnection           (optional — requires wail-core[google])
        └── CalendarIntegration        (business logic: groups, filtering)
                └── BookingIntegration (business logic: slots, booking, cancellation)

    StripePaymentConnection            (optional — requires wail-core[stripe])
        └── PaymentIntegration         (business logic: sessions, webhook dispatch)
                └── register_action("create_booking", ...)
                        → BookingIntegration.create_booking (post-payment hook)

If the optional extras are not installed, the corresponding connections and
integrations are silently skipped — the server still starts and any installed
plugin integrations remain active.
"""

from connections.base import AbstractCalendarConnection
from connections.payment import AbstractPaymentConnection
from integrations.calendar import CalendarIntegration
from integrations.booking import BookingIntegration
from integrations.payment import PaymentIntegration


def _discover_entrypoint_plugins() -> list:
    """
    Auto-discover installed WAIL plugins via the ``wail.plugins`` entry point group.

    Plugin packages declare themselves in their pyproject.toml::

        [project.entry-points."wail.plugins"]
        hubspot = "hubspot_integration:plugin_integration"

    The entry point value is loaded and must be either:
    - An AbstractIntegration **instance** (preferred — pre-configured with credentials).
    - An AbstractIntegration **subclass** — instantiated with no arguments.

    Failed plugins (import errors, instantiation errors) are skipped with a
    warning printed to stderr so a broken third-party plugin never prevents WAIL
    from starting.

    Returns:
        List of AbstractIntegration instances, one per successfully loaded plugin.
    """
    import sys
    from integrations.base import AbstractIntegration

    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="wail.plugins")
    except Exception:
        return []

    loaded: list = []
    for ep in eps:
        try:
            obj = ep.load()
        except Exception as exc:
            print(
                f"[WAIL] Warning: failed to import plugin '{ep.name}' "
                f"from '{ep.value}': {exc}",
                file=sys.stderr,
            )
            continue

        if isinstance(obj, AbstractIntegration):
            loaded.append(obj)
        elif isinstance(obj, type) and issubclass(obj, AbstractIntegration):
            try:
                loaded.append(obj())
            except Exception as exc:
                print(
                    f"[WAIL] Warning: plugin '{ep.name}' ({obj.__name__}) "
                    f"could not be instantiated with no arguments: {exc}\n"
                    "       Set the entry point to a pre-built instance instead of the class.",
                    file=sys.stderr,
                )
        else:
            print(
                f"[WAIL] Warning: plugin '{ep.name}' does not point to an "
                "AbstractIntegration class or instance — skipping.",
                file=sys.stderr,
            )

    return loaded


class WailApp:
    """
    Central application object. Holds all connections and integrations as
    instance variables so they can be inspected, swapped, or reconfigured
    at runtime without touching module-level globals.

    Google Calendar and Stripe connections are created lazily — if their
    packages (``wail-core[google]`` / ``wail-core[stripe]``) are not
    installed, those integrations are simply absent and everything else
    continues to work normally.

    All credentials live on the connection objects (e.g. wail.stripe_payment_conn).
    Use set_credentials() on each connection — or the admin API routes —
    to change credentials without restarting the server.

    Args:
        calendar_conn:      Override the default GoogleCalendarConnection.
                            Pass an AbstractCalendarConnection subclass to
                            replace Google with another provider.
        payment_conn:       Override the default StripePaymentConnection.
        extra_integrations: Explicit AbstractIntegration instances to inject.
                            Merged with any auto-discovered entry-point plugins.
        discover_plugins:   If True (default), auto-load all installed packages
                            that declare a ``wail.plugins`` entry point. Set to
                            False in tests or when you want full manual control
                            over which integrations are active.

    Plugin example (no core changes required)::

        from integrations.registry import WailApp
        from wail_hubspot import HubspotIntegration

        # Explicit injection (always works):
        wail_app = WailApp(extra_integrations=[HubspotIntegration()])

        # Or just install the package — WAIL discovers it automatically:
        #   pip install wail-plugin-hubspot
        wail_app = WailApp()   # HubspotIntegration loaded via entry points
    """

    def __init__(
        self,
        calendar_conn:      "AbstractCalendarConnection | None" = None,
        payment_conn:       "AbstractPaymentConnection  | None" = None,
        extra_integrations: "list | None"                       = None,
        discover_plugins:   bool                                = True,
    ) -> None:
        # ── Calendar connection (requires wail-core[google]) ──────────────────
        if calendar_conn is not None:
            self.google_calendar_conn = calendar_conn
        else:
            try:
                from connections.google_calendar import GoogleCalendarConnection
                self.google_calendar_conn = GoogleCalendarConnection()
            except ImportError:
                self.google_calendar_conn = None

        # ── Payment connection (requires wail-core[stripe]) ───────────────────
        if payment_conn is not None:
            self.stripe_payment_conn = payment_conn
        else:
            try:
                from connections.stripe_payment import StripePaymentConnection
                self.stripe_payment_conn = StripePaymentConnection()
            except ImportError:
                self.stripe_payment_conn = None

        # ── Integrations (built on top of connections) ─────────────────────────
        if self.google_calendar_conn is not None:
            self.calendar_integration = CalendarIntegration(
                connection=self.google_calendar_conn,
            )
            self.booking_integration = BookingIntegration(
                calendar=self.calendar_integration,
            )
        else:
            self.calendar_integration = None
            self.booking_integration  = None

        if self.stripe_payment_conn is not None:
            self.payment_integration = PaymentIntegration(
                connection=self.stripe_payment_conn,
            )
        else:
            self.payment_integration = None

        # ── Post-payment action wiring ─────────────────────────────────────────
        if self.payment_integration and self.booking_integration:
            self.payment_integration.register_action(
                "create_booking",
                lambda payload: self.booking_integration.create_booking(
                    payload["date"],
                    payload["time"],
                    payload["service_id"],
                    payload,
                    group=payload.get("group"),
                ),
            )

        # ── Plugin integrations ────────────────────────────────────────────────
        discovered = _discover_entrypoint_plugins() if discover_plugins else []
        self._extra_integrations: list = [
            *list(extra_integrations or []),
            *discovered,
        ]

    # ── Multi-tenant factory (deprecated) ────────────────────────────────────
    #
    # Superseded by TenantMiddleware + TenantCredentials (tenant.py).
    # One WailApp singleton now serves all tenants; credentials are injected
    # per-request via a contextvars.ContextVar so no per-tenant files are
    # created. This classmethod will be removed in a future release.

    @classmethod
    def for_tenant(
        cls,
        tenant_id: str,
        extra_integrations: "list | None" = None,
    ) -> "WailApp":
        """
        .. deprecated::
            Use ``TenantMiddleware`` + ``InMemoryTenantStore`` (or a custom
            ``AbstractTenantStore``) instead.  A single ``WailApp`` singleton
            handles all tenants; credentials are injected per-request from the
            async context variable set by the middleware.

            See ``tenant.py`` for a complete migration guide.
        """
        import warnings
        warnings.warn(
            "WailApp.for_tenant() is deprecated and will be removed in a future "
            "release. Use TenantMiddleware with an AbstractTenantStore instead — "
            "see tenant.py for a complete migration guide.",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            from pathlib import Path
            from credentials import CredentialStore
            from connections.google_calendar import GoogleCalendarConnection
            from connections.stripe_payment import StripePaymentConnection
            _backend_dir = Path(__file__).parent.parent
            cred_store = CredentialStore(path=_backend_dir / f"credentials_{tenant_id}.json")
            cal_conn   = GoogleCalendarConnection(
                credential_store=cred_store,
                token_path=_backend_dir / f"token_{tenant_id}.json",
            )
            pay_conn   = StripePaymentConnection(credential_store=cred_store)
        except ImportError:
            cal_conn = None
            pay_conn = None
        return cls(
            calendar_conn=cal_conn,
            payment_conn=pay_conn,
            extra_integrations=extra_integrations,
        )

    # ── All integrations list ─────────────────────────────────────────────────
    #
    # Used by management endpoints (GET /integrations, enable/disable toggles),
    # the MCP adapter, and the discovery API.  Built-in integrations come first;
    # plugin integrations follow in the order they were passed in.
    # None entries (connections not installed) are excluded automatically.

    @property
    def ALL_INTEGRATIONS(self) -> list:
        built_ins = [
            self.calendar_integration,
            self.booking_integration,
            self.payment_integration,
        ]
        return [i for i in built_ins if i is not None] + self._extra_integrations


# ── Application singleton ─────────────────────────────────────────────────────
#
# This is the single live instance used by the entire server.
# Import `wail` to access it, or import the named aliases below for brevity.

wail = WailApp()


# ── Backward-compatible module-level aliases ──────────────────────────────────
#
# These are the names the routers import. They point at the live instance so
# any change made via set_credentials() etc. is reflected everywhere immediately.
# Values may be None if the corresponding optional extra is not installed.

google_calendar_conn = wail.google_calendar_conn
stripe_payment_conn  = wail.stripe_payment_conn

calendar_integration = wail.calendar_integration
booking_integration  = wail.booking_integration
payment_integration  = wail.payment_integration

ALL_INTEGRATIONS     = wail.ALL_INTEGRATIONS
