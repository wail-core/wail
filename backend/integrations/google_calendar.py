"""
Compatibility shim — redirects to the new module locations.

Code that imported from here (evals.py, old scripts) continues to work
without modification.  New code should import directly from:

    connections.google_calendar   — GoogleCalendarConnection
    integrations.calendar         — CalendarIntegration
    integrations.registry         — google_calendar_conn, calendar_integration
"""

from integrations.registry import google_calendar_conn, calendar_integration  # noqa: F401

# `google_calendar` is the name evals.py expects
google_calendar = google_calendar_conn


def load_groups() -> list[dict]:
    return calendar_integration.get_groups()


def save_groups(groups: list[dict]) -> None:
    calendar_integration.save_groups(groups)
