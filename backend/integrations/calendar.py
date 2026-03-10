"""
CalendarIntegration — business logic layer over an AbstractCalendarConnection.

Responsibilities:
  - Groups (workers): load/save config, classify events into groups using the
    existing filter engine so booking and calendar views share the same group definitions.
  - Event normalization: map RawCalendarEvent → WAIL's normalized dict schema,
    with per-field schema control.
  - Filtering: apply generic filter rules to normalized events.
  - Agent-facing fetch_events / fetch_events_for_group methods.
  - fetch_day_events: convenience method used by BookingIntegration so booking
    availability checks automatically respect group classifications.

The connection is injected at construction time, so this class works with any
AbstractCalendarConnection implementation (Google, Outlook, iCal, …).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from connections.base import AbstractCalendarConnection
from integrations.base import AbstractIntegration, _make_http_helpers

GROUPS_PATH = Path(__file__).parent.parent / "groups_config.json"

# Default normalized schema — which fields are included unless overridden
SCHEMA_DEFAULTS: dict[str, bool] = {
    "id":             True,
    "title":          True,
    "start":          True,
    "end":            True,
    "location":       True,
    "status":         True,
    "description":    False,
    "attendee_count": False,
}


class CalendarIntegration(AbstractIntegration):
    """
    High-level calendar integration.

    Wraps an AbstractCalendarConnection and adds WAIL-specific features:
    groups, filtering, and a normalized event schema.  BookingIntegration
    depends on this class so that groups defined here flow automatically
    into booking availability checks.

    Connection contract: expects an AbstractCalendarConnection.
    """

    name:         str = "calendar"
    display_name: str = "Calendar"
    description:  str = "View calendar events and manage worker/resource groups."

    def __init__(self, connection: AbstractCalendarConnection) -> None:
        self.connection = connection

    def get_manifest_extras(self) -> dict:
        return {
            "connected":   self.connection.connected,
            "connect_url": "/auth/google",
        }

    # ── Groups (workers) ─────────────────────────────────────────

    def get_groups(self) -> list[dict]:
        """Return the configured worker groups, or [] if none."""
        if not GROUPS_PATH.exists():
            return []
        try:
            return json.loads(GROUPS_PATH.read_text()).get("groups", [])
        except Exception:
            return []

    def save_groups(self, groups: list[dict]) -> None:
        GROUPS_PATH.write_text(json.dumps({"groups": groups}, indent=2))

    def classify_event(self, event_dict: dict, groups: list[dict]) -> str | None:
        """
        Return the name of the first group whose match rules the event satisfies,
        or None if the event doesn't match any group.

        Reuses the existing filter engine so groups and dashboard filters share
        one rule language.
        """
        from integrations.filters import apply_filters
        for group in groups:
            if apply_filters([event_dict], group.get("match", [])):
                return group["name"]
        return None

    # ── Normalization ─────────────────────────────────────────────

    def _normalize(self, raw, schema: dict) -> dict:
        """Convert a RawCalendarEvent to a WAIL normalized dict."""
        extractors = {
            "id":             lambda e: e.id,
            "title":          lambda e: e.title,
            "start":          lambda e: e.start,
            "end":            lambda e: e.end,
            "location":       lambda e: e.location,
            "description":    lambda e: e.description,
            "status":         lambda e: e.status,
            "attendee_count": lambda e: e.attendee_count,
        }
        return {
            field: extractor(raw)
            for field, extractor in extractors.items()
            if schema.get(field, SCHEMA_DEFAULTS.get(field, True))
        }

    # ── Agent-facing fetch methods ────────────────────────────────

    def fetch_events(self, settings: dict) -> list[dict]:
        """
        Fetch, normalize, filter, and group-classify calendar events.

        settings keys:
            calendar_id  (str)                — default "primary"
            days_ahead   ({ enabled, value }) — look-ahead window; disabled = open-ended
            max_events   ({ enabled, value }) — result cap; disabled = up to 2500
            schema       (dict)               — field → bool override
            filters      (list)               — [{field, operator, value}, …]
        """
        from integrations.filters import apply_filters

        calendar_id = settings.get("calendar_id", "primary")
        schema      = settings.get("schema", {})
        filters     = settings.get("filters", [])

        def _ev(key, default):
            raw = settings.get(key)
            if isinstance(raw, dict):
                return raw.get("enabled", True), raw.get("value", default)
            if raw is not None:
                return True, raw
            return True, default

        days_enabled, days_value = _ev("days_ahead", 7)
        max_enabled,  max_value  = _ev("max_events", 20)

        now      = datetime.now(timezone.utc)
        time_max = (now + timedelta(days=int(days_value))) if days_enabled else None

        raw_events = self.connection.fetch_events(
            calendar_id = calendar_id,
            time_min    = now,
            time_max    = time_max,
            max_results = int(max_value) if max_enabled else 2500,
        )

        normalized = [self._normalize(e, schema) for e in raw_events]
        result     = apply_filters(normalized, filters)

        groups = self.get_groups()
        if groups:
            for event in result:
                event["group"] = self.classify_event(event, groups)

        return result

    def fetch_events_for_group(self, group_name: str, settings: dict) -> list[dict]:
        """Fetch events and return only those classified under group_name."""
        groups = self.get_groups()
        if not any(g["name"].lower() == group_name.lower() for g in groups):
            raise ValueError(f"Group '{group_name}' is not configured")
        events = self.fetch_events(settings)
        return [e for e in events if (e.get("group") or "").lower() == group_name.lower()]

    def fetch_day_events(
        self,
        calendar_id: str,
        date_str:    str,
        timezone_name: str = "UTC",
    ) -> list[dict]:
        """
        Fetch all events for a single local business day, with group classification.

        Used by BookingIntegration so slot availability automatically knows which
        events belong to which worker — no duplication of group logic needed.

        The time window is computed in the business's local timezone so events near
        midnight are never missed due to UTC offset.
        """
        from zoneinfo import ZoneInfo
        from datetime import date as Date

        d  = Date.fromisoformat(date_str)
        tz = ZoneInfo(timezone_name)

        day_start = datetime(d.year, d.month, d.day,  0,  0, tzinfo=tz).astimezone(timezone.utc)
        day_end   = datetime(d.year, d.month, d.day, 23, 59, tzinfo=tz).astimezone(timezone.utc)

        raw_events = self.connection.fetch_events(
            calendar_id = calendar_id,
            time_min    = day_start,
            time_max    = day_end,
            max_results = 200,
        )

        # Use full schema for booking (start/end are what matter; title helps with groups)
        full_schema = {k: True for k in SCHEMA_DEFAULTS}
        normalized  = [self._normalize(e, full_schema) for e in raw_events]

        # Classify into groups — same rules as the calendar view
        groups = self.get_groups()
        if groups:
            for event in normalized:
                event["group"] = self.classify_event(event, groups)

        return normalized

    # ── Calendar list ─────────────────────────────────────────────

    def list_calendars(self) -> list[dict]:
        return [
            {"id": c.id, "name": c.name, "primary": c.primary}
            for c in self.connection.list_calendars()
        ]

    # ── AbstractIntegration implementation ────────────────────────

    def get_settings_schema(self) -> dict:
        """JSON Schema for the groups configuration."""
        return {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "description": (
                        "Worker or resource groups. Each event is classified into "
                        "the first group whose match rules it satisfies."
                    ),
                    "items": {
                        "type": "object",
                        "required": ["name", "match"],
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Group name, e.g. 'Alice'",
                            },
                            "match": {
                                "type": "array",
                                "description": "Filter rules — all must match for the event to belong to this group.",
                                "items": {
                                    "type": "object",
                                    "required": ["field", "operator", "value"],
                                    "properties": {
                                        "field":    {"type": "string"},
                                        "operator": {
                                            "type": "string",
                                            "enum": [
                                                "contains", "not_contains",
                                                "equals", "not_equals",
                                                "starts_with", "ends_with",
                                            ],
                                        },
                                        "value": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

    def get_mcp_tools(self, wail_base: str, api_key: str) -> list:
        """Expose calendar-viewing tools to MCP agents."""
        _get, _post, _ = _make_http_helpers(wail_base, api_key)

        def get_calendar_events(days_ahead: int = 7, group: str = "") -> dict:
            """
            Get upcoming booked appointments from the calendar.

            Args:
                days_ahead: How many days ahead to fetch (default 7).
                group:      Optional worker name to filter to a specific worker's events.

            Use this to see what's already booked. To find open times, use
            get_available_slots instead.
            """
            body: dict = {"days_ahead": {"enabled": True, "value": days_ahead}}
            if group:
                return _post(f"/integrations/google-calendar/events/{group}", body)
            return _post("/integrations/google-calendar/events", body)

        def get_worker_groups() -> dict:
            """
            Get the list of configured worker groups (e.g. individual barbers).

            Each group has a name. Pass the group name to get_available_slots or
            book_appointment to target a specific worker.
            """
            return _get("/integrations/google-calendar/groups")

        return [get_calendar_events, get_worker_groups]
