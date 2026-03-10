"""
Abstract bases and validated data models for WAIL connections.

A "connection" is a thin adapter to one external service.  It handles
authentication and raw CRUD operations only — no business logic.

Higher-level behaviour lives in the integrations layer.

Hierarchy
---------
AbstractConnection               ← implement this for any new service type
    └── AbstractCalendarConnection  ← calendar-specific sub-ABC
            └── GoogleCalendarConnection (concrete)

Data models (Pydantic)
----------------------
All data crossing the connection→integration boundary is validated by Pydantic.
If a connection returns a value that fails validation (wrong type, missing field),
WAIL raises a ValidationError before the bad data reaches the agent.

To add a non-calendar service (e.g. Shopify):
    class ShopifyConnection(AbstractConnection):
        name = "shopify"
        display_name = "Shopify"
        ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field


# ── Universal connection base ─────────────────────────────────────────────────

class AbstractConnection(ABC):
    """
    Minimal contract every WAIL connection must satisfy.

    Rules:
    - No business logic — only auth and raw API calls.
    - Raise RuntimeError if not connected when an API call is attempted.
    - Subclasses set `name` and `display_name` as ClassVar strings.
    """

    name:         ClassVar[str]   # machine slug,   e.g. "google_calendar"
    display_name: ClassVar[str]   # human label,    e.g. "Google Calendar"

    @property
    @abstractmethod
    def connected(self) -> bool:
        """True if a valid, usable credential exists."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Revoke/remove the stored credential."""
        ...


# ── Shared data models (Pydantic) ─────────────────────────────────────────────
#
# These models are the contract between connections and integrations.
# Pydantic validates and coerces all fields on construction — bad data from a
# third-party connector is caught here, not downstream in business logic.

class RawCalendarEvent(BaseModel):
    """Normalized event as returned by a connection, before any WAIL processing."""

    id:             str
    title:          str
    start:          str   # ISO 8601 string (dateTime or date)
    end:            str   # ISO 8601 string (dateTime or date)
    description:    str = ""
    location:       str = ""
    status:         str = ""
    attendee_count: int = 0


class NewCalendarEvent(BaseModel):
    """Event to be created via a connection."""

    title:       str
    description: str
    start_dt:    datetime   # must be timezone-aware
    end_dt:      datetime   # must be timezone-aware
    timezone:    str = "UTC"  # IANA timezone name stored with the event


class CalendarInfo(BaseModel):
    """A calendar the user has access to."""

    id:      str
    name:    str
    primary: bool = False


# ── Calendar-specific sub-ABC ─────────────────────────────────────────────────

class AbstractCalendarConnection(AbstractConnection):
    """
    Implement this class for each external calendar service.

    Adds calendar CRUD operations on top of AbstractConnection.
    All datetime arguments that represent wall-clock time must be timezone-aware.
    Return types are Pydantic models — WAIL validates them automatically.

    To add a new calendar backend (Outlook, iCal, …):
    1. Subclass AbstractCalendarConnection.
    2. Set name / display_name class variables.
    3. Implement every abstractmethod below.
    4. Wire up the singleton in integrations/registry.py.
    """

    # ── Event CRUD ───────────────────────────────────────────────────────────

    @abstractmethod
    def fetch_events(
        self,
        calendar_id: str,
        time_min:    datetime,
        time_max:    datetime | None = None,
        max_results: int = 200,
    ) -> list[RawCalendarEvent]:
        """
        Fetch events in [time_min, time_max].
        time_max=None means no upper bound.
        """
        ...

    @abstractmethod
    def get_event(self, calendar_id: str, event_id: str) -> RawCalendarEvent:
        """Fetch a single event by ID."""
        ...

    @abstractmethod
    def create_event(self, calendar_id: str, event: NewCalendarEvent) -> str:
        """Create an event; return its ID."""
        ...

    @abstractmethod
    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Permanently delete an event."""
        ...

    # ── Calendar discovery ───────────────────────────────────────────────────

    @abstractmethod
    def list_calendars(self) -> list[CalendarInfo]:
        """List all calendars accessible to the connected account."""
        ...
