"""
Booking integration — slot availability and appointment management.

Two layers live here:

1. Pure helper functions (no I/O, no external deps beyond stdlib + zoneinfo).
   These can be tested in isolation and reused by anything that needs booking logic.

2. BookingIntegration class — orchestrates the helpers with a CalendarIntegration
   so that booking automatically inherits the calendar's group/worker definitions.
   All Google Calendar API calls flow through the injected CalendarIntegration's
   connection, so swapping the calendar backend requires zero changes here.

Config file (booking_config.json next to backend root):
    {
      "enabled": true,
      "calendar_id": "primary",
      "timezone": "America/New_York",
      "slot_interval_minutes": 15,
      "buffer_minutes": 0,
      "advance_booking_days": 30,
      "services": [{"id": "haircut", "name": "Haircut", "duration_minutes": 30, "price": 25}],
      "hours": {"monday": {"open": "09:00", "close": "19:00"}, ..., "sunday": null},
      "fields": [
        {"id": "name",  "label": "Full Name",     "required": true},
        {"id": "email", "label": "Email Address", "required": true},
        {"id": "phone", "label": "Phone Number",  "required": false},
        {"id": "notes", "label": "Notes",         "required": false}
      ],
      "trust_field_id": "email",
      "confirmation_template": "Appointment confirmed: {service} on {date} at {time} for {name}."
    }

Booking request body (POST /booking/book):
    { date, time, service_id, group (optional), <field_id>: value, … }

Cancellation body (DELETE /booking/book/{event_id}):
    { "contact": "<trust-field value used when booking>" }
    Cancellation only works for WAIL-created events where the contact matches.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from datetime import date as Date
from pathlib import Path
from zoneinfo import ZoneInfo

from integrations.base import AbstractIntegration, _make_http_helpers

BOOKING_CONFIG_PATH = Path(__file__).parent.parent / "booking_config.json"

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Embedded in every WAIL-created event description so cancellation can be verified
WAIL_MARKER  = "WAIL-Booking: true"
WAIL_CONTACT = "WAIL-Contact: "

# Booking-specific defaults.
# timezone and hours live in shared_config.json (accessible by all integrations).
DEFAULT_CONFIG: dict = {
    "enabled": True,
    "calendar_id": "primary",
    "slot_interval_minutes": 15,
    "buffer_minutes": 0,
    "advance_booking_days": 30,
    "services": [
        {"id": "haircut",        "name": "Haircut",         "duration_minutes": 30, "price": 25},
        {"id": "beard-trim",     "name": "Beard Trim",      "duration_minutes": 20, "price": 15},
        {"id": "haircut-beard",  "name": "Haircut + Beard", "duration_minutes": 50, "price": 40},
        {"id": "hot-towel-shave","name": "Hot Towel Shave", "duration_minutes": 40, "price": 35},
        {"id": "kids-cut",       "name": "Kids Cut",        "duration_minutes": 20, "price": 18},
    ],
    "fields": [
        {"id": "name",  "label": "Full Name",               "required": True},
        {"id": "email", "label": "Email Address",           "required": True},
        {"id": "phone", "label": "Phone Number",            "required": False},
        {"id": "notes", "label": "Notes / Special Requests","required": False},
    ],
    "trust_field_id": "email",
    "confirmation_template": (
        "Appointment confirmed: {service} on {date} at {time} for {name}. "
        "We look forward to seeing you!"
    ),
}


# ── Config persistence ─────────────────────────────────────────────

def load_booking_config() -> dict:
    """Load booking-specific config (services, fields, intervals, etc.)."""
    if not BOOKING_CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(BOOKING_CONFIG_PATH.read_text())
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_booking_config(config: dict) -> None:
    BOOKING_CONFIG_PATH.write_text(json.dumps(config, indent=2))


def get_effective_booking_config() -> dict:
    """
    Merge shared business config into booking config.

    Shared settings (timezone, hours, notifications) are authoritative —
    any integration that updates shared_config.json (e.g. GHL syncing new
    schedule) automatically affects booking without any extra configuration.

    Booking-specific settings (services, fields, intervals, etc.) stay in
    booking_config.json and are not affected by shared config.
    """
    from shared_config import load_shared_config
    shared  = load_shared_config()
    booking = load_booking_config()
    return {
        **booking,
        "timezone":             shared.get("timezone", "UTC"),
        "hours":                shared.get("hours", {}),
        "business_name":        shared.get("business_name", ""),
        "notifications_enabled": shared.get("notifications", {}).get("enabled", True),
    }


# ── Pure helpers ───────────────────────────────────────────────────

def _time_to_minutes(time_str: str) -> int:
    h, m = map(int, time_str.split(":"))
    return h * 60 + m


def _minutes_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _event_span_on_date(ev: dict, target_date: Date, tz_name: str = "UTC") -> tuple[int, int] | None:
    """
    Return (start_minutes, end_minutes) in local business time for an event
    that falls on target_date, or None if it doesn't.
    """
    start_str = ev.get("start")
    end_str   = ev.get("end")
    if not start_str or not end_str:
        return None
    try:
        tz       = ZoneInfo(tz_name)
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(tz)
        end_dt   = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(tz)
        if start_dt.date() != target_date:
            return None
        return (start_dt.hour * 60 + start_dt.minute, end_dt.hour * 60 + end_dt.minute)
    except Exception:
        return None


def _get_service(config: dict, service_id: str) -> dict:
    svc = next((s for s in config.get("services", []) if s["id"] == service_id), None)
    if not svc:
        raise ValueError(f"Service '{service_id}' not found in booking config")
    return svc


def validate_booking_fields(body: dict, config: dict) -> list[str]:
    """Return human-readable labels of missing required fields."""
    return [
        f.get("label") or f["id"]
        for f in config.get("fields", [])
        if f.get("required") and not (body.get(f["id"]) or "").strip()
    ]


def get_trust_contact(body: dict, config: dict) -> str:
    """Value of the trust field — used for allowlist checking and cancellation auth."""
    field_id = config.get("trust_field_id", "email")
    return (body.get(field_id) or body.get("customer_contact") or "").strip()


def compute_available_slots(
    date_str:   str,
    service_id: str,
    config:     dict,
    events:     list[dict],
    group:      str | None = None,
) -> list[str]:
    """
    Return available HH:MM start times for date_str and service_id.

    events — normalized events for that day (from CalendarIntegration.fetch_day_events).
    group  — when set, only that worker's events count as busy so you get per-worker
             availability.  Events are already classified by CalendarIntegration.
    """
    target_date = Date.fromisoformat(date_str)
    day_name    = target_date.strftime("%A").lower()
    tz_name     = config.get("timezone", "UTC")

    hours = config.get("hours", {}).get(day_name)
    if not hours:
        return []

    service  = _get_service(config, service_id)
    duration = service["duration_minutes"]
    interval = config.get("slot_interval_minutes", 15)
    buffer   = config.get("buffer_minutes", 0)
    open_m   = _time_to_minutes(hours["open"])
    close_m  = _time_to_minutes(hours["close"])

    filtered = (
        [e for e in events if (e.get("group") or "").lower() == group.lower()]
        if group else events
    )

    busy: list[tuple[int, int]] = []
    for ev in filtered:
        span = _event_span_on_date(ev, target_date, tz_name)
        if span:
            busy.append(span)

    available: list[str] = []
    current = open_m
    while current + duration <= close_m:
        slot_end = current + duration
        if not any(current < b_end + buffer and slot_end > b_start - buffer for b_start, b_end in busy):
            available.append(_minutes_to_time(current))
        current += interval

    return available


def verify_cancellation(raw_description: str, contact: str) -> tuple[bool, str]:
    """
    Verify an event is a WAIL booking and the contact matches.
    Returns (ok, error_message).
    """
    if WAIL_MARKER not in raw_description:
        return False, "This event was not created by WAIL and cannot be cancelled through this API."

    stored = ""
    for line in raw_description.splitlines():
        if line.startswith(WAIL_CONTACT):
            stored = line[len(WAIL_CONTACT):].strip()
            break

    if stored.lower() != contact.lower().strip():
        return False, "Contact does not match the booking record."

    return True, ""


def format_confirmation(
    service:  dict,
    date_str: str,
    time_str: str,
    body:     dict,
    config:   dict,
) -> str:
    template = config.get(
        "confirmation_template",
        "Appointment confirmed: {service} on {date} at {time}.",
    )
    d    = Date.fromisoformat(date_str)
    subs = {f["id"]: (body.get(f["id"]) or "") for f in config.get("fields", [])}
    subs["service"]       = service["name"]
    subs["date"]          = f"{d.strftime('%A, %B')} {d.day}"
    subs["time"]          = time_str
    subs["customer_name"] = subs.get("name", "")  # backward-compat alias
    try:
        return template.format(**subs)
    except KeyError:
        return template


# ── BookingIntegration ─────────────────────────────────────────────

class BookingIntegration(AbstractIntegration):
    """
    Orchestrates booking logic over a CalendarIntegration.

    By depending on CalendarIntegration (not the connection directly), booking
    automatically inherits whatever group/worker definitions the calendar has.
    fetch_day_events() is called through CalendarIntegration so events are
    already classified — no duplication of group logic here.

    This class does NOT perform trust checks — that is the HTTP layer's responsibility.

    Connection contract: depends on a CalendarIntegration (not a raw connection),
    so that groups defined in the calendar layer flow automatically into slot
    availability without any duplicated configuration.
    """

    name:         str = "booking"
    display_name: str = "Booking"
    description:  str = "Manage appointment bookings: availability, scheduling, and cancellation."

    def __init__(self, calendar: "CalendarIntegration") -> None:  # noqa: F821
        self.calendar = calendar

    # ── Config ───────────────────────────────────────────────────

    def load_config(self) -> dict:
        """Booking-specific config only (services, fields, intervals, etc.)."""
        return load_booking_config()

    def load_effective_config(self) -> dict:
        """
        Full merged config including shared settings (timezone, hours).

        Use this for any operation that needs timezone or business hours —
        they live in shared_config.json so all integrations stay in sync.
        """
        return get_effective_booking_config()

    def save_config(self, config: dict) -> None:
        save_booking_config(config)

    # ── Availability ──────────────────────────────────────────────

    def get_available_slots(
        self,
        date_str:   str,
        service_id: str,
        group:      str | None = None,
    ) -> tuple[list[str], dict]:
        """
        Return (available_slots, effective_config).

        Fetches the day's events through CalendarIntegration so group
        classification is applied automatically.  Uses the effective config
        so timezone and hours always come from shared business settings.
        """
        config = self.load_effective_config()
        events = self.calendar.fetch_day_events(
            calendar_id   = config.get("calendar_id", "primary"),
            date_str      = date_str,
            timezone_name = config.get("timezone", "UTC"),
        )
        slots = compute_available_slots(date_str, service_id, config, events, group=group)
        return slots, config

    # ── Create booking ─────────────────────────────────────────────

    def create_booking(
        self,
        date_str:   str,
        time_str:   str,
        service_id: str,
        body:       dict,
        group:      str | None = None,
    ) -> dict:
        """
        Validate fields, verify the slot is still free, create the calendar event.

        Raises:
            ValueError  — missing fields, service not found, or slot unavailable
        """
        from connections.base import NewCalendarEvent

        config = self.load_effective_config()

        missing = validate_booking_fields(body, config)
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        service = _get_service(config, service_id)

        # Re-fetch events through the calendar layer so groups are already classified
        events = self.calendar.fetch_day_events(
            calendar_id   = config.get("calendar_id", "primary"),
            date_str      = date_str,
            timezone_name = config.get("timezone", "UTC"),
        )
        available = compute_available_slots(date_str, service_id, config, events, group=group)
        if time_str not in available:
            raise ValueError(f"{time_str} is not available. Available slots: {available}")

        # Build event data
        tz_name  = config.get("timezone", "UTC")
        tz       = ZoneInfo(tz_name)
        h, m     = map(int, time_str.split(":"))
        d        = Date.fromisoformat(date_str)
        start_dt = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        end_dt   = start_dt + timedelta(minutes=service["duration_minutes"])

        desc_lines: list[str] = []
        for field_cfg in config.get("fields", []):
            val = (body.get(field_cfg["id"]) or "").strip()
            if val:
                desc_lines.append(f"{field_cfg.get('label', field_cfg['id'])}: {val}")
        if group:
            desc_lines.append(f"Worker: {group}")
        desc_lines += ["", WAIL_MARKER, f"{WAIL_CONTACT}{get_trust_contact(body, config)}"]

        name    = (body.get("name") or body.get("customer_name") or "").strip()
        title   = f"Appointment - {service['name']}" + (f" — {name}" if name else "")

        calendar_id = config.get("calendar_id", "primary")
        event_id = self.calendar.connection.create_event(
            calendar_id,
            NewCalendarEvent(
                title       = title,
                description = "\n".join(desc_lines),
                start_dt    = start_dt,
                end_dt      = end_dt,
                timezone    = tz_name,
            ),
        )

        result: dict = {
            "status":           "booked",
            "event_id":         event_id,
            "service":          service["name"],
            "date":             date_str,
            "day":              d.strftime("%A"),
            "time":             time_str,
            "duration_minutes": service["duration_minutes"],
            "customer_name":    name,
            "customer_contact": get_trust_contact(body, config),
            "group":            group,
        }

        # Only include the confirmation message when notifications are enabled.
        # Set shared_config notifications.enabled = false if your CRM handles
        # its own confirmations and you don't want duplicates.
        if config.get("notifications_enabled", True):
            result["confirmation"] = format_confirmation(service, date_str, time_str, body, config)

        return result

    # ── Cancel booking ─────────────────────────────────────────────

    def cancel_booking(self, event_id: str, contact: str) -> None:
        """
        Cancel a WAIL-created booking.

        Raises:
            LookupError    — event not found
            PermissionError — event is not a WAIL booking or contact doesn't match
        """
        config      = self.load_config()
        calendar_id = config.get("calendar_id", "primary")

        try:
            raw = self.calendar.connection.get_event(calendar_id, event_id)
        except Exception:
            raise LookupError("Event not found")

        ok, err = verify_cancellation(raw.description, contact)
        if not ok:
            raise PermissionError(err)

        self.calendar.connection.delete_event(calendar_id, event_id)

    # ── AbstractIntegration implementation ────────────────────────

    def get_settings_schema(self) -> dict:
        """
        JSON Schema for booking-specific configuration.

        Note: timezone, business hours, and notification settings are shared
        across all integrations and are configured at GET/POST /config.
        """
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Enable or disable the booking system.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Google Calendar ID to read/write bookings (default: 'primary').",
                },
                "slot_interval_minutes": {
                    "type": "integer",
                    "description": "Minutes between slot start times, e.g. 15 gives :00 :15 :30 :45 slots.",
                },
                "buffer_minutes": {
                    "type": "integer",
                    "description": "Padding added around each booking to prevent back-to-back appointments.",
                },
                "advance_booking_days": {
                    "type": "integer",
                    "description": "How many days ahead customers are allowed to book.",
                },
                "services": {
                    "type": "array",
                    "description": "Bookable services offered by the business.",
                    "items": {
                        "type": "object",
                        "required": ["id", "name", "duration_minutes"],
                        "properties": {
                            "id":   {"type": "string", "description": "Unique service slug, e.g. 'haircut'."},
                            "name": {"type": "string", "description": "Display name shown to customers."},
                            "duration_minutes": {
                                "type": "integer",
                                "description": "How long the service takes (determines slot blocking).",
                            },
                            "price": {
                                "type": "number",
                                "description": "Price in the currency configured in payment settings. Set to 0 for free services.",
                            },
                        },
                    },
                },
                "fields": {
                    "type": "array",
                    "description": "Customer info fields collected at booking time.",
                    "items": {
                        "type": "object",
                        "required": ["id", "label"],
                        "properties": {
                            "id":       {"type": "string",  "description": "Field key used in booking requests, e.g. 'email'."},
                            "label":    {"type": "string",  "description": "Human-readable label shown to customers."},
                            "required": {"type": "boolean", "description": "Whether this field must be filled in before booking."},
                        },
                    },
                },
                "trust_field_id": {
                    "type": "string",
                    "description": (
                        "The field whose value is used as the trust contact for cancellation auth "
                        "and allowlist checks. Typically 'email'."
                    ),
                },
                "confirmation_template": {
                    "type": "string",
                    "description": (
                        "Message returned on a successful booking (when notifications are enabled). "
                        "Supports {service}, {date}, {time}, and any field id as placeholders, "
                        "e.g. {name}, {email}."
                    ),
                },
            },
        }

    def get_mcp_tools(self, wail_base: str, api_key: str) -> list:
        """Expose booking tools to MCP agents."""
        _get, _post, _delete = _make_http_helpers(wail_base, api_key)

        def get_booking_config() -> dict:
            """
            Get the booking configuration for this business.

            Returns services (with id, name, duration_minutes, price), business
            hours, required booking fields, timezone, and slot settings.

            Always call this before booking to discover valid service_ids and
            required field ids.
            """
            return _get("/integrations/google-calendar/booking/config")

        def get_available_slots(date: str, service_id: str, group: str = "") -> dict:
            """
            Get available appointment start times for a date and service.

            Args:
                date:       Date in YYYY-MM-DD format, e.g. "2024-03-15".
                service_id: Service ID from get_booking_config, e.g. "haircut".
                group:      Optional worker name to check only that worker's
                            availability. Omit to see general availability.

            Returns available_slots (list of "HH:MM" strings) and service details.
            """
            body: dict = {"date": date, "service_id": service_id}
            if group:
                body["group"] = group
            return _post("/integrations/google-calendar/booking/slots", body)

        def book_appointment(
            date:       str,
            time:       str,
            service_id: str,
            fields:     dict,
            group:      str = "",
        ) -> dict:
            """
            Create a booking. Call get_booking_config first to learn required
            fields, and get_available_slots to confirm the time is free.

            Args:
                date:       Date in YYYY-MM-DD format.
                time:       Start time in HH:MM format — must be an available slot.
                service_id: Service ID from get_booking_config.
                fields:     Customer details keyed by field id.
                            e.g. {"name": "Alex", "email": "alex@example.com"}
                            Required field ids are listed in get_booking_config under fields.
                group:      Optional worker name to book with a specific worker.

            Returns status, event_id (save for cancellation), and a confirmation message.
            """
            body: dict = {"date": date, "time": time, "service_id": service_id, **fields}
            if group:
                body["group"] = group
            return _post("/integrations/google-calendar/booking/book", body)

        def cancel_booking(event_id: str, contact: str) -> dict:
            """
            Cancel a WAIL-created booking.

            Args:
                event_id: The event_id returned by book_appointment.
                contact:  The value of the trust field submitted when booking
                          (typically the customer's email). Proves the caller
                          owns the booking — others' bookings cannot be cancelled.

            Only bookings created through WAIL can be cancelled with this tool.
            """
            return _delete(
                f"/integrations/google-calendar/booking/book/{event_id}",
                {"contact": contact},
            )

        return [get_booking_config, get_available_slots, book_appointment, cancel_booking]
