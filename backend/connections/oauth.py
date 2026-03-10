"""
AbstractOAuthConnection — base class for OAuth2 authorization-code connections.

Developers building a new OAuth-backed connection subclass this instead of
AbstractConnection. It standardises:

  - Credential and token file injection (single-tenant default or per-tenant).
  - The auth-URL → code-exchange → token-save lifecycle.
  - Token refresh on access (override _refresh_token if the library handles it).
  - The FastAPI route helpers that mount /auth/{slug} and /auth/{slug}/callback
    onto any APIRouter without boilerplate.

Quick-start — build a new OAuth connection::

    from pathlib import Path
    from connections.oauth import AbstractOAuthConnection

    class OutlookCalendarConnection(AbstractOAuthConnection):
        name         = "outlook_calendar"
        display_name = "Outlook Calendar"
        scopes       = ["Calendars.Read", "Calendars.ReadWrite"]

        # Implement the three abstract methods:
        def _build_auth_url(self) -> str:
            ...  # use self.client_id, self.client_secret, self.scopes

        def _exchange_code(self, code: str) -> dict:
            ...  # returns a token dict; _save_token() persists it

        def _refresh_token(self) -> bool:
            ...  # refreshes self._token, calls _save_token(); return True on success

        # Use self._get_token_field("access_token") to read the stored token.

    # Wire into WailApp:
    wail_app = WailApp(calendar_conn=OutlookCalendarConnection())

    # Or for multi-tenant use WailApp.for_tenant() which injects per-tenant
    # CredentialStore and token_path automatically.

The auth routes are mounted automatically when you include the router::

    from connections.oauth import mount_oauth_routes
    from fastapi import APIRouter

    router = APIRouter()
    mount_oauth_routes(router, conn=my_outlook_conn, redirect_after="/dashboard")
    app.include_router(router)
"""

from __future__ import annotations

import json
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar

from connections.base import AbstractConnection
from credentials import CredentialStore, credential_store as _default_cred_store

_BACKEND_DIR = Path(__file__).parent.parent


class AbstractOAuthConnection(AbstractConnection):
    """
    Base class for OAuth2 authorization-code connections.

    Subclasses must:
    1. Set ``name``, ``display_name``, and ``scopes`` class variables.
    2. Implement ``_build_auth_url()``, ``_exchange_code()``, and
       ``_refresh_token()``.

    Everything else (credential injection, token persistence, the
    ``connected`` property, ``disconnect()``) is handled here.

    Args:
        credential_store: CredentialStore instance. Defaults to the module-level
                          singleton. Pass a per-tenant store for multi-tenancy.
        token_path:       Path to the JSON file where the OAuth token is saved.
                          Defaults to ``token_{name}.json`` in the backend dir.
    """

    name:         ClassVar[str]
    display_name: ClassVar[str]
    scopes:       ClassVar[list[str]] = []

    def __init__(
        self,
        credential_store: CredentialStore | None = None,
        token_path:       Path | None            = None,
    ) -> None:
        self._cred_store  = credential_store or _default_cred_store
        self._token_path  = token_path or (_BACKEND_DIR / f"token_{self.name}.json")
        self._token: dict = self._load_token()

        # Subclasses read client credentials via self.client_id / self.client_secret
        self.client_id     = self._cred_store.get(f"{self.name.upper()}_CLIENT_ID")
        self.client_secret = self._cred_store.get(f"{self.name.upper()}_CLIENT_SECRET")

    # ── Token persistence ─────────────────────────────────────────────────────

    def _load_token(self) -> dict:
        if not self._token_path.exists():
            return {}
        try:
            return json.loads(self._token_path.read_text())
        except Exception:
            return {}

    def _save_token(self, token: dict) -> None:
        self._token = token
        self._token_path.write_text(json.dumps(token, indent=2))

    def _get_token_field(self, key: str, default: str = "") -> str:
        """Read a field from the stored token dict."""
        return str(self._token.get(key, default))

    # ── Credential management ─────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """True if OAuth client credentials (client_id + secret) are set."""
        return bool(self.client_id) and bool(self.client_secret)

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """
        Update OAuth client credentials and persist to the credential store.

        Takes effect immediately — no server restart required.
        The user must re-authorize via get_auth_url() after changing these.
        """
        self.client_id     = client_id
        self.client_secret = client_secret
        self._cred_store.set(
            **{
                f"{self.name.upper()}_CLIENT_ID":     client_id,
                f"{self.name.upper()}_CLIENT_SECRET": client_secret,
            }
        )

    # ── AbstractConnection contract ───────────────────────────────────────────

    @property
    def connected(self) -> bool:
        """
        True if a valid, usable token exists.

        Attempts a token refresh when the stored token looks expired.
        Override ``_refresh_token()`` with provider-specific refresh logic.
        """
        if not self._token:
            return False
        try:
            return self._refresh_token()
        except Exception:
            return False

    def disconnect(self) -> None:
        """Delete the stored OAuth token. Client credentials are kept."""
        self._token = {}
        if self._token_path.exists():
            self._token_path.unlink()

    # ── OAuth lifecycle — subclasses implement these ──────────────────────────

    @abstractmethod
    def _build_auth_url(self) -> str:
        """
        Build and return the provider's authorization URL.

        Use self.client_id, self.client_secret, and self.scopes.
        Raise RuntimeError if client credentials are not set.
        """
        ...

    @abstractmethod
    def _exchange_code(self, code: str) -> None:
        """
        Exchange the authorization code for an access/refresh token.

        Call self._save_token(token_dict) to persist the result.
        """
        ...

    @abstractmethod
    def _refresh_token(self) -> bool:
        """
        Attempt to refresh the stored token.

        Returns True if the token is valid (already fresh or successfully
        refreshed). Returns False if the token cannot be refreshed.
        Call self._save_token(new_token_dict) if a new token is obtained.
        """
        ...

    # ── Public OAuth helpers ──────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        """Return the OAuth authorization URL. Raises RuntimeError if not configured."""
        if not self.configured:
            raise RuntimeError(
                f"{self.display_name} OAuth client credentials are not set. "
                f"POST the client_id and client_secret via the admin API first."
            )
        return self._build_auth_url()

    def exchange_code(self, code: str) -> None:
        """Exchange the authorization code received in the OAuth callback."""
        self._exchange_code(code)


# ── Route helper ──────────────────────────────────────────────────────────────

def mount_oauth_routes(
    router,
    conn: AbstractOAuthConnection,
    *,
    redirect_after: str = "/",
    require_admin=None,
) -> None:
    """
    Mount GET /auth/{conn.name} and GET /auth/{conn.name}/callback onto *router*.

    This replaces hand-written auth routes for every new OAuth connection —
    one call mounts both routes automatically.

    Args:
        router:         FastAPI APIRouter to mount routes onto.
        conn:           The AbstractOAuthConnection instance to drive.
        redirect_after: URL to redirect to after a successful exchange.
                        Typically a dashboard page, e.g. "/dashboard?connected=true".
        require_admin:  Optional FastAPI dependency (e.g. ``require_admin`` from
                        routers/deps.py) to protect the initiation route.

    Example::

        from fastapi import APIRouter
        from connections.oauth import mount_oauth_routes
        from routers.deps import require_admin

        router = APIRouter()
        mount_oauth_routes(
            router,
            conn=my_connection,
            redirect_after="/dashboard?connected=true",
            require_admin=require_admin,
        )
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse, RedirectResponse

    slug        = conn.name
    deps        = [Depends(require_admin)] if require_admin else []

    @router.get(f"/auth/{slug}", dependencies=deps, tags=["Auth"])
    async def _initiate():
        try:
            return RedirectResponse(conn.get_auth_url())
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get(f"/auth/{slug}/callback", tags=["Auth"])
    async def _callback(code: str):
        conn.exchange_code(code)
        return RedirectResponse(redirect_after)

    # Rename functions so FastAPI generates unique operationIds
    _initiate.__name__ = f"auth_{slug}_initiate"
    _callback.__name__  = f"auth_{slug}_callback"
