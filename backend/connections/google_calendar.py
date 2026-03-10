"""
Google Calendar connection — implements AbstractCalendarConnection.

Handles OAuth2 and all raw Google Calendar API calls.

Credentials are set via the admin API and stored in credentials.json:
    GOOGLE_CLIENT_ID     — OAuth client ID from Google Cloud Console
    GOOGLE_CLIENT_SECRET — OAuth client secret from Google Cloud Console

Falls back to environment variables (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
for existing .env-based setups.

OAuth token (access + refresh) is stored in token.json after the user
completes the consent flow at GET /auth/google.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

# Allow OAuth over plain HTTP in local dev
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from connections.base import (
    AbstractCalendarConnection,
    RawCalendarEvent,
    NewCalendarEvent,
    CalendarInfo,
)
from credentials import credential_store as _default_credential_store, CredentialStore

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
REDIRECT_URI = "http://localhost:8000/auth/google/callback"

_DEFAULT_TOKEN_PATH = Path(__file__).parent.parent / "token.json"


def _save_token(creds: Credentials, path: Path) -> None:
    path.write_text(creds.to_json())


def _load_token(path: Path) -> Credentials | None:
    if not path.exists():
        return None
    try:
        return Credentials.from_authorized_user_info(
            json.loads(path.read_text()), SCOPES
        )
    except Exception:
        return None


class GoogleCalendarConnection(AbstractCalendarConnection):
    """
    Adapter for the Google Calendar API.

    OAuth client credentials (client_id, client_secret) are stored as
    instance variables. Call set_credentials() to update them at runtime;
    they are persisted to credentials.json immediately.

    The OAuth access/refresh token is stored separately in token.json
    and loaded automatically on startup.

    Args:
        credential_store: Injected CredentialStore. Defaults to the module-level
                          singleton (single-tenant). Pass a per-tenant instance for
                          multi-tenant deployments.
        token_path:       Path to the OAuth token file. Defaults to token.json in
                          the backend directory. Override for per-tenant isolation.
    """

    name:         ClassVar[str] = "google_calendar"
    display_name: ClassVar[str] = "Google Calendar"

    def __init__(
        self,
        credential_store: "CredentialStore | None" = None,
        token_path:       Path | None              = None,
    ) -> None:
        self._cred_store  = credential_store or _default_credential_store
        self._token_path  = token_path or _DEFAULT_TOKEN_PATH
        # Fallback credentials for single-tenant mode (no TenantMiddleware).
        # Multi-tenant: these are ignored; _resolve_*() reads from context.
        self._client_id     = self._cred_store.get("GOOGLE_CLIENT_ID")
        self._client_secret = self._cred_store.get("GOOGLE_CLIENT_SECRET")
        self._creds: Credentials | None = _load_token(self._token_path)

    # ── Context-aware credential resolution ──────────────────────────────────
    #
    # In multi-tenant mode, TenantMiddleware sets _tenant_ctx per request.
    # These methods return the per-tenant value when set, falling back to the
    # instance's stored credentials for single-tenant / no-middleware setups.

    def _resolve_client_id(self) -> str:
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        return (ctx.google_client_id if ctx and ctx.google_client_id
                else self._client_id)

    def _resolve_client_secret(self) -> str:
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        return (ctx.google_client_secret if ctx and ctx.google_client_secret
                else self._client_secret)

    def _resolve_creds(self) -> Credentials | None:
        """Return a Credentials object from the tenant context, or the stored one."""
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        if ctx and ctx.google_token:
            try:
                return Credentials.from_authorized_user_info(ctx.google_token, SCOPES)
            except Exception:
                pass
        return self._creds

    def _persist_refreshed_token(self, creds: Credentials) -> None:
        """
        Save a refreshed token back to both the tenant context and the token file.

        Called after a successful token refresh so the new access token is not
        lost at the end of the request.
        """
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        if ctx:
            # Mutate in-place; TenantMiddleware writes it back to the store.
            import json as _json
            ctx.google_token = _json.loads(creds.to_json())
        else:
            # Single-tenant: persist to token file as before
            _save_token(creds, self._token_path)

    # ── Credential management ─────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """True if OAuth client credentials (client_id + secret) are set."""
        return bool(self._resolve_client_id()) and bool(self._resolve_client_secret())

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """
        Update OAuth client credentials and persist to credentials.json (single-tenant).

        Takes effect immediately — no server restart required.
        """
        self._client_id     = client_id
        self._client_secret = client_secret
        self._cred_store.set(
            GOOGLE_CLIENT_ID=client_id,
            GOOGLE_CLIENT_SECRET=client_secret,
        )

    # ── Connection state ─────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        """True if a valid OAuth token exists and is usable."""
        creds = self._resolve_creds()
        if creds is None:
            return False
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                self._persist_refreshed_token(creds)
                # Keep the single-tenant instance in sync
                self._creds = creds
                return True
            except Exception:
                return False
        return creds.valid

    def disconnect(self) -> None:
        """Revoke the stored OAuth token. Client credentials are kept."""
        self._creds = None
        if self._token_path.exists():
            self._token_path.unlink()

    # ── OAuth flow ───────────────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        if not self.configured:
            raise RuntimeError(
                "Google OAuth client credentials are not set. "
                "POST /integrations/google-calendar/credentials with "
                "{ client_id, client_secret } first."
            )
        flow = Flow.from_client_config(self._client_config(), scopes=SCOPES)
        flow.redirect_uri = REDIRECT_URI
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return auth_url

    def exchange_code(self, code: str) -> None:
        flow = Flow.from_client_config(self._client_config(), scopes=SCOPES)
        flow.redirect_uri = REDIRECT_URI
        flow.fetch_token(code=code)
        self._creds = flow.credentials
        _save_token(self._creds, self._token_path)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _client_config(self) -> dict:
        """Build the OAuth client config dict from stored instance credentials."""
        return {
            "web": {
                "client_id":     self._client_id,
                "client_secret": self._client_secret,
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        }

    def _svc(self):
        """Return an authenticated Google Calendar service client."""
        if not self.connected:
            raise RuntimeError("Not connected to Google Calendar.")
        return build("calendar", "v3", credentials=self._creds)

    @staticmethod
    def _to_raw(e: dict) -> RawCalendarEvent:
        return RawCalendarEvent(
            id            = e.get("id", ""),
            title         = e.get("summary", ""),
            start         = (e.get("start", {}).get("dateTime") or e.get("start", {}).get("date") or ""),
            end           = (e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date") or ""),
            description   = e.get("description", ""),
            location      = e.get("location", ""),
            status        = e.get("status", ""),
            attendee_count= len(e.get("attendees", [])),
        )

    # ── AbstractCalendarConnection implementation ─────────────────────────────

    def fetch_events(
        self,
        calendar_id: str,
        time_min:    datetime,
        time_max:    datetime | None = None,
        max_results: int = 200,
    ) -> list[RawCalendarEvent]:
        kwargs: dict = {
            "calendarId":   calendar_id,
            "timeMin":      time_min.isoformat(),
            "maxResults":   max_results,
            "singleEvents": True,
            "orderBy":      "startTime",
        }
        if time_max is not None:
            kwargs["timeMax"] = time_max.isoformat()

        result = self._svc().events().list(**kwargs).execute()
        return [self._to_raw(e) for e in result.get("items", [])]

    def get_event(self, calendar_id: str, event_id: str) -> RawCalendarEvent:
        e = self._svc().events().get(calendarId=calendar_id, eventId=event_id).execute()
        return self._to_raw(e)

    def create_event(self, calendar_id: str, event: NewCalendarEvent) -> str:
        body = {
            "summary":     event.title,
            "description": event.description,
            "start":       {"dateTime": event.start_dt.isoformat(), "timeZone": event.timezone},
            "end":         {"dateTime": event.end_dt.isoformat(),   "timeZone": event.timezone},
        }
        result = self._svc().events().insert(calendarId=calendar_id, body=body).execute()
        return result.get("id", "")

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._svc().events().delete(calendarId=calendar_id, eventId=event_id).execute()

    def list_calendars(self) -> list[CalendarInfo]:
        result = self._svc().calendarList().list().execute()
        return [
            CalendarInfo(
                id      = item["id"],
                name    = item.get("summary", item["id"]),
                primary = item.get("primary", False),
            )
            for item in result.get("items", [])
        ]
