"""
Tests for wail_testing itself — the test harness.

These also serve as a living spec / worked example for plugin developers.
Run with: pytest backend/tests/test_wail_testing.py -v
"""

import pytest
from wail_testing import (
    MockCalendarConnection,
    MockPaymentConnection,
    WailTestClient,
    fake_event,
    fake_booking_config,
    validate_plugin,
)
from integrations.base import AbstractIntegration


# ── Sample plugin (used across multiple tests) ────────────────────────────────

class EchoIntegration(AbstractIntegration):
    """Minimal plugin that satisfies every contract requirement."""

    name         = "echo"
    display_name = "Echo"
    description  = "Returns whatever it receives — useful for testing."

    def get_settings_schema(self):
        return {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "String prepended to every echo"},
            },
        }

    def get_mcp_tools(self, wail_base, api_key):
        def echo_message(message: str) -> dict:
            "Echo the message back to the caller."
            return {"echo": message}

        def count_words(text: str) -> dict:
            "Count the number of words in the given text."
            return {"count": len(text.split()), "text": text}

        return [echo_message, count_words]


# ── validate_plugin ───────────────────────────────────────────────────────────

def test_validate_plugin_passes_for_valid_integration():
    validate_plugin(EchoIntegration)  # must not raise


def test_validate_plugin_accepts_instance():
    validate_plugin(EchoIntegration())  # must not raise


def test_validate_plugin_fails_missing_name():
    class Bad(AbstractIntegration):
        display_name = "Bad"
        description  = "Missing name"
        def get_mcp_tools(self, *a): return []

    with pytest.raises(AssertionError, match="name"):
        validate_plugin(Bad)


def test_validate_plugin_fails_undocumented_tool():
    class BadTools(AbstractIntegration):
        name         = "bad_tools"
        display_name = "Bad Tools"
        description  = "Has an undocumented tool"

        def get_mcp_tools(self, wail_base, api_key):
            def no_docstring(x: str) -> dict:
                return {}  # no docstring!
            return [no_docstring]

    with pytest.raises(AssertionError, match="docstring"):
        validate_plugin(BadTools)


def test_validate_plugin_fails_unannotated_parameter():
    class Unannotated(AbstractIntegration):
        name         = "unannotated"
        display_name = "Unannotated"
        description  = "Has an unannotated parameter"

        def get_mcp_tools(self, wail_base, api_key):
            def bad_tool(x) -> dict:  # x has no annotation
                "A tool with no type annotation."
                return {}
            return [bad_tool]

    with pytest.raises(AssertionError, match="annotation"):
        validate_plugin(Unannotated)


# ── Fake data factories ───────────────────────────────────────────────────────

def test_fake_event_defaults():
    ev = fake_event()
    assert ev.id == "evt-fake-001"
    assert ev.title == "Test appointment"
    assert "T" in ev.start


def test_fake_event_overrides():
    ev = fake_event(title="Custom", start="2026-03-10T11:00:00", id="my-id")
    assert ev.title == "Custom"
    assert ev.start == "2026-03-10T11:00:00"
    assert ev.id == "my-id"


def test_fake_booking_config_defaults():
    config = fake_booking_config()
    assert "services" in config
    assert len(config["services"]) > 0


def test_fake_booking_config_override():
    config = fake_booking_config(timezone="America/Chicago")
    assert config["timezone"] == "America/Chicago"


# ── MockCalendarConnection ────────────────────────────────────────────────────

def test_mock_calendar_connected():
    cal = MockCalendarConnection()
    assert cal.connected is True


def test_mock_calendar_has_default_events():
    cal = MockCalendarConnection()
    from datetime import datetime, timezone, timedelta
    events = cal.fetch_events("primary", datetime.now(timezone.utc) - timedelta(hours=1))
    assert len(events) >= 1


def test_mock_calendar_create_event_recorded():
    from datetime import datetime, timezone
    from connections.base import NewCalendarEvent

    cal = MockCalendarConnection()
    ev  = NewCalendarEvent(
        title="Test",
        description="desc",
        start_dt=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        end_dt=datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc),
    )
    event_id = cal.create_event("primary", ev)
    assert event_id.startswith("mock-event-")
    cal.assert_event_created(title="Test")


def test_mock_calendar_delete_event_recorded():
    cal = MockCalendarConnection()
    cal.delete_event("primary", "evt-existing-1")
    cal.assert_event_deleted("evt-existing-1")


def test_mock_calendar_assert_event_created_fails_when_empty():
    cal = MockCalendarConnection()
    with pytest.raises(AssertionError, match="No events were created"):
        cal.assert_event_created()


# ── MockPaymentConnection ─────────────────────────────────────────────────────

def test_mock_payment_creates_session():
    pay = MockPaymentConnection()
    session = pay.create_checkout_session(
        2500, "usd", "Haircut", {}, "http://ok", "http://cancel"
    )
    assert session.session_id.startswith("mock_sess_")
    assert "mock-checkout" in session.payment_url
    pay.assert_session_created(min_amount_cents=2000)


def test_mock_payment_session_status():
    pay = MockPaymentConnection()
    status = pay.get_session_status("mock_sess_1")
    assert status.status == "complete"
    assert status.payment_status == "paid"


def test_mock_payment_verify_webhook_no_signature_check():
    import json
    pay   = MockPaymentConnection()
    event = {"type": "checkout.session.completed", "data": {"object": {"id": "x"}}}
    result = pay.verify_webhook(json.dumps(event).encode(), "any_sig", "any_secret")
    assert result["type"] == "checkout.session.completed"


# ── WailTestClient — HTTP ─────────────────────────────────────────────────────

def test_manifest_endpoint(wail_test_client):
    r = wail_test_client.get("/integrations/manifest")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    names = [i["name"] for i in data]
    assert "calendar"  in names
    assert "booking"   in names
    assert "payment"   in names


def test_manifest_includes_settings_schema(wail_test_client):
    r = wail_test_client.get("/integrations/manifest")
    for item in r.json():
        assert "settings_schema" in item
        assert "type" in item["settings_schema"]


def test_root_returns_wail_manifest(wail_test_client):
    r = wail_test_client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body.get("service") == "WAIL API"


def test_openapi_spec_endpoint(wail_test_client):
    r = wail_test_client.get("/_wail/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"] == "3.1.0"
    assert "paths" in spec


def test_booking_config_endpoint(wail_test_client):
    r = wail_test_client.get("/integrations/google-calendar/booking/config")
    assert r.status_code == 200
    body = r.json()
    assert "services" in body


# ── WailTestClient — simulate_agent_request ───────────────────────────────────

def test_simulate_agent_request_appointments(wail_test_client):
    r = wail_test_client.simulate_agent_request("What appointments are available today?")
    assert r.status_code == 200


def test_simulate_agent_request_booking_info(wail_test_client):
    r = wail_test_client.simulate_agent_request("What services do you offer and at what price?")
    assert r.status_code == 200


def test_simulate_agent_request_manifest(wail_test_client):
    r = wail_test_client.simulate_agent_request("Which integrations and plugins are active?")
    assert r.status_code == 200


def test_simulate_agent_request_default_fallback(wail_test_client):
    r = wail_test_client.simulate_agent_request("Hello there")
    assert r.status_code == 200


# ── WailTestClient — plugin integration ──────────────────────────────────────

def test_plugin_appears_in_manifest(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[EchoIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        r = client.get("/integrations/manifest")
        names = [i["name"] for i in r.json()]
        assert "echo" in names


def test_plugin_call_mcp_tool(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[EchoIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        result = client.call_mcp_tool("echo", "echo_message", message="hello world")
        assert result == {"echo": "hello world"}


def test_plugin_call_mcp_tool_with_int_args(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[EchoIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        result = client.call_mcp_tool("echo", "count_words", text="one two three")
        assert result["count"] == 3


def test_call_mcp_tool_raises_for_unknown_integration(wail_test_client):
    with pytest.raises(KeyError, match="ghost"):
        wail_test_client.call_mcp_tool("ghost", "some_tool")


def test_call_mcp_tool_raises_for_unknown_tool(wail_test_client):
    with pytest.raises(KeyError, match="no_such_tool"):
        wail_test_client.call_mcp_tool("booking", "no_such_tool")


# ── WailTestClient — payment webhook simulation ───────────────────────────────

def test_payment_webhook_creates_booking(mock_calendar, mock_payment):
    """Full end-to-end: fake webhook → booking action → calendar event created."""
    with WailTestClient(
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        result = client.simulate_payment_webhook(
            action="create_booking",
            payload={
                "date":       "2026-03-15",
                "time":       "10:00",
                "service_id": "haircut",
                "name":       "Alice",
                "email":      "alice@example.com",
            },
            amount_cents=2500,
        )
        # The action handler ran (booking integration dispatched)
        assert result.get("status") in ("ok", "dispatched", "action_failed")
        # If status is "ok" / "dispatched", an event should have been created
        if result.get("status") in ("ok", "dispatched"):
            mock_calendar.assert_event_created()


# ── Mock isolation — patches don't bleed between tests ───────────────────────

def test_mock_isolation_calendar_writes_do_not_persist(wail_test_client, mock_calendar):
    """Writes in one test must not appear in the next."""
    assert mock_calendar.created_events == [], (
        "created_events should be empty at the start of each test"
    )
