"""
Stateless multi-tenancy for WAIL.

Architecture
------------
One WailApp singleton serves the entire process. Tenant credentials are not
stored on connection objects — they are loaded per-request from a TenantStore,
placed in a contextvars.ContextVar, and read by connections as needed.

This means:
  - No per-tenant WailApp instances (no memory bloat at 1000 tenants).
  - No credential files on the server filesystem (cloud / Docker safe).
  - Connection objects are stateless singletons — they never own credentials.

Three-layer design::

    ┌─────────────────────────────┐
    │  TenantMiddleware           │  reads X-WAIL-Tenant header
    │  (Starlette middleware)     │  calls TenantStore.get(tenant_id)
    │                             │  sets _tenant_ctx ContextVar
    └────────────┬────────────────┘
                 │
    ┌────────────▼────────────────┐
    │  _tenant_ctx ContextVar     │  holds TenantCredentials for this request
    │  (per async task)           │  isolated across concurrent requests
    └────────────┬────────────────┘
                 │
    ┌────────────▼────────────────┐
    │  Connection._resolve_*()    │  reads from context; falls back to
    │  (GoogleCalendar, Stripe…)  │  CredentialStore for single-tenant mode
    └─────────────────────────────┘

Quick-start: single-tenant (existing behaviour, nothing to change)
------------------------------------------------------------------
If no TenantMiddleware is added, the context var is never set and connections
fall back to their CredentialStore as before. Zero migration cost.

Quick-start: multi-tenant
-------------------------
    from tenant import TenantMiddleware, InMemoryTenantStore, TenantCredentials

    store = InMemoryTenantStore()
    await store.save(TenantCredentials(
        tenant_id="acme",
        google_client_id="...", google_client_secret="...",
        google_token={"access_token": "...", "refresh_token": "...", ...},
        stripe_secret_key="sk_live_...",
        stripe_webhook_secret="whsec_...",
    ))

    app.add_middleware(TenantMiddleware, store=store)

Requests from "acme" must send ``X-WAIL-Tenant: acme``.  The connections pick
up the right credentials automatically — no other code change needed.

Persisting refreshed tokens
---------------------------
The middleware writes back updated credentials after each request, so if Google
refreshes the OAuth access token mid-request, the new token is persisted
automatically.

Custom backends (Redis, Postgres, Vault)
-----------------------------------------
Subclass AbstractTenantStore and implement get / save / delete::

    class RedisTenantStore(AbstractTenantStore):
        async def get(self, tenant_id):
            raw = await redis.get(f"wail:tenant:{tenant_id}")
            return TenantCredentials.model_validate_json(raw) if raw else None

        async def save(self, creds):
            await redis.set(f"wail:tenant:{creds.tenant_id}",
                            creds.model_dump_json(), ex=86400)

        async def delete(self, tenant_id):
            await redis.delete(f"wail:tenant:{tenant_id}")

    app.add_middleware(TenantMiddleware, store=RedisTenantStore())
"""

from __future__ import annotations

import contextvars
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


# ── TenantCredentials ─────────────────────────────────────────────────────────

class TenantCredentials(BaseModel):
    """
    All credentials belonging to one tenant, passed through the request context.

    Core fields cover the built-in Google Calendar and Stripe connections.
    Plugins add their own credentials to the ``extra`` dict using their own
    namespaced keys (e.g. ``extra["hubspot_api_key"]``).

    All fields are optional so that partially-configured tenants can still
    make read-only calls against whatever they have connected.
    """

    tenant_id: str = "default"

    # ── Google Calendar ───────────────────────────────────────────────────────
    google_client_id:     str  = ""
    google_client_secret: str  = ""
    # Stored as the dict produced by google.oauth2.credentials.Credentials.to_json()
    # (or its parsed equivalent). Empty dict = not yet authorised.
    google_token:         dict = Field(default_factory=dict)

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_secret_key:      str = ""
    stripe_webhook_secret:  str = ""

    # ── Plugin extensions ─────────────────────────────────────────────────────
    # Plugins store their own credentials here with namespaced keys.
    # e.g. extra["hubspot_api_key"] = "pat-na1-..."
    extra: dict[str, Any] = Field(default_factory=dict)


# ── Context variable ──────────────────────────────────────────────────────────

#: Per-request context variable.  Set by TenantMiddleware before the handler
#: runs; read by connection._resolve_*() methods.  Never set outside of a
#: request context (will be None, triggering the CredentialStore fallback).
_tenant_ctx: contextvars.ContextVar[TenantCredentials | None] = (
    contextvars.ContextVar("_wail_tenant", default=None)
)


def get_current_tenant() -> TenantCredentials | None:
    """Return the TenantCredentials for the current async task, or None."""
    return _tenant_ctx.get()


# ── AbstractTenantStore ───────────────────────────────────────────────────────

class AbstractTenantStore(ABC):
    """
    Pluggable credential backend.

    Implement this to store tenant credentials in Redis, Postgres, AWS
    Secrets Manager, or any other backing store.

    All methods are async so that I/O-bound stores (network databases) don't
    block the event loop.
    """

    @abstractmethod
    async def get(self, tenant_id: str) -> TenantCredentials | None:
        """
        Return credentials for *tenant_id*, or None if unknown.

        Called once per request by TenantMiddleware before the handler runs.
        """
        ...

    @abstractmethod
    async def save(self, creds: TenantCredentials) -> None:
        """
        Persist *creds*.

        Called by TenantMiddleware after the handler completes, so refreshed
        OAuth tokens are written back automatically.
        """
        ...

    @abstractmethod
    async def delete(self, tenant_id: str) -> None:
        """Remove all credentials for *tenant_id*."""
        ...


# ── InMemoryTenantStore ───────────────────────────────────────────────────────

class InMemoryTenantStore(AbstractTenantStore):
    """
    In-process credential store — suitable for development and single-server
    deployments.  Data is lost on restart.

    For production, implement AbstractTenantStore with a durable backend
    (Redis, Postgres, Vault) and pass it to TenantMiddleware.
    """

    def __init__(self) -> None:
        self._store: dict[str, TenantCredentials] = {}

    async def get(self, tenant_id: str) -> TenantCredentials | None:
        return self._store.get(tenant_id)

    async def save(self, creds: TenantCredentials) -> None:
        self._store[creds.tenant_id] = creds

    async def delete(self, tenant_id: str) -> None:
        self._store.pop(tenant_id, None)

    def list_tenants(self) -> list[str]:
        """Return all registered tenant IDs (convenience method for admin tooling)."""
        return list(self._store.keys())


# ── TenantMiddleware ──────────────────────────────────────────────────────────

class TenantMiddleware:
    """
    Starlette / FastAPI middleware that wires per-tenant credentials into each
    request's async context.

    Flow per request:
    1. Read ``X-WAIL-Tenant`` header (defaults to ``"default"`` if absent).
    2. Look up credentials in *store*.
    3. Set the ``_tenant_ctx`` ContextVar so connections can read it.
    4. Call the next handler.
    5. Write back the (possibly updated) credentials — captures token refreshes.

    If the tenant is unknown, the request proceeds with an empty
    TenantCredentials so single-tenant deployments (no header) still work.

    Args:
        app:   The ASGI application to wrap.
        store: AbstractTenantStore implementation to query.

    Example::

        from tenant import TenantMiddleware, InMemoryTenantStore

        store = InMemoryTenantStore()
        app.add_middleware(TenantMiddleware, store=store)
    """

    def __init__(self, app, *, store: AbstractTenantStore) -> None:
        self.app   = app
        self.store = store

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract tenant ID from request headers
        headers   = dict(scope.get("headers", []))
        tenant_id = headers.get(b"x-wail-tenant", b"default").decode().strip() or "default"

        # Load credentials; fall back to an empty record for unknown tenants
        creds = await self.store.get(tenant_id) or TenantCredentials(tenant_id=tenant_id)

        # Set context var (scoped to this async task)
        token = _tenant_ctx.set(creds)
        try:
            await self.app(scope, receive, send)
        finally:
            # Write back in case a connection refreshed a token mid-request
            updated = _tenant_ctx.get()
            if updated is not None:
                await self.store.save(updated)
            _tenant_ctx.reset(token)


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_tenant_credentials_dep():
    """
    FastAPI dependency — return the TenantCredentials for the current request.

    Use this in routes that need to act on a specific tenant's data directly
    (e.g. admin credential management endpoints).

    For most routes, connections resolve credentials automatically via the
    context var — no explicit dependency injection needed.

    Example::

        from fastapi import Depends
        from tenant import get_tenant_credentials_dep

        @router.get("/my-route")
        async def route(creds=Depends(get_tenant_credentials_dep)):
            return {"tenant": creds.tenant_id}
    """
    creds = _tenant_ctx.get()
    return creds or TenantCredentials()
