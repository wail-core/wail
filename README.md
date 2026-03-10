# WAIL — Website Agent Integration Layer

WAIL is an open framework for exposing any business to AI agents (Claude, ChatGPT, Gemini, etc.) via their native protocols.

Instead of each agent needing to know about each site, you register your site with WAIL once. WAIL becomes the unified directory — a single MCP server, OpenAPI Action, or Gemini Extension that gives any agent access to all registered sites and their capabilities.

```
[AI Agent] ──→ [WAIL Server] ──→ [Connector Layer] ──→ [Client's data sources]
                     │
              [Protocol Adapters]
              MCP / OpenAPI / Gemini
```

---

## What's included

| Layer | What it does |
|---|---|
| **Connections** | Thin adapters to external services (Google Calendar, Stripe, …). Auth only — no business logic. |
| **Integrations** | Business logic built on top of connections (booking slots, payment sessions, …). |
| **Protocol Adapters** | Translate integrations to MCP tools, OpenAPI Actions, or Gemini Extensions. |
| **Registry** | Dashboard for site owners to connect their accounts and configure each integration. |
| **Multi-tenancy** | One server instance serves every registered site. Credentials are injected per-request via a `ContextVar` — no per-tenant processes or files. |
| **Plugin system** | Third-party packages extend WAIL with new integrations. Auto-discovered via Python entry points — no code change needed in core. |

---

## Stack

- **Backend** — Python 3.10+, FastAPI, Pydantic v2
- **Frontend** — Next.js (dashboard)

---

## Quickstart

### Backend

```bash
cd backend
pip install -e ".[all]"      # installs wail-core plus all optional extras
cp .env.example .env         # fill in your Google OAuth client credentials
wail-serve                   # starts the API at http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                  # starts the dashboard at http://localhost:3000
```

### Optional extras

```bash
pip install wail-core                  # core only (FastAPI + abstract bases)
pip install "wail-core[google]"        # + Google Calendar connection
pip install "wail-core[stripe]"        # + Stripe payment connection
pip install "wail-core[mcp]"           # + MCP protocol adapter
pip install "wail-core[redis]"         # + Redis cache backend
pip install "wail-core[dev]"           # + wail-dev sandbox (hot-reload)
pip install "wail-core[test]"          # + pytest fixtures + mocks
pip install "wail-core[all]"           # everything
```

---

## Architecture deep-dive

### Connections vs Integrations

These are two separate concepts that are easy to conflate.

A **Connection** is a thin, stateless adapter to one external service. It knows only about authentication and raw CRUD. It has no opinion about what a "booking" is or how slots are computed.

An **Integration** is business logic built *on top of* a connection. It knows about your domain model — availability windows, service IDs, cancellation rules — and exposes that logic to agents via MCP tools.

```
AbstractConnection               ← implement for any new service type
    └── AbstractCalendarConnection  ← calendar-specific sub-ABC
            └── GoogleCalendarConnection  (concrete, lives in connections/)

AbstractIntegration              ← implement for any new feature set
    ├── CalendarIntegration      (reads/filters events)
    └── BookingIntegration       (computes slots, creates/cancels bookings)
```

### Request flow

```
HTTP request
    │
    ▼
TenantMiddleware          reads X-WAIL-Tenant header
    │                     loads TenantCredentials from TenantStore
    │                     sets _tenant_ctx ContextVar
    ▼
FastAPI router            validates request, calls integration method
    │
    ▼
Integration               business logic (BookingIntegration, etc.)
    │
    ▼
Connection._resolve_*()   reads credentials from _tenant_ctx first,
    │                     falls back to CredentialStore (single-tenant)
    ▼
External API              Google Calendar, Stripe, etc.
```

### Multi-tenancy

WAIL uses a single application instance to serve every tenant. Credentials are injected per-request — not stored on connection objects.

```python
from tenant import TenantMiddleware, InMemoryTenantStore, TenantCredentials

store = InMemoryTenantStore()
await store.save(TenantCredentials(
    tenant_id="acme",
    google_client_id="...",
    google_client_secret="...",
    google_token={"access_token": "...", "refresh_token": "..."},
    stripe_secret_key="sk_live_...",
))

app.add_middleware(TenantMiddleware, store=store)
```

Requests identify themselves with `X-WAIL-Tenant: acme`. The connections pick up the right credentials automatically. If a Google OAuth token refreshes mid-request, the updated token is written back to the store in the middleware's `finally` block.

For production, subclass `AbstractTenantStore` to back the store with Redis, Postgres, or Vault:

```python
class RedisTenantStore(AbstractTenantStore):
    async def get(self, tenant_id):
        raw = await redis.get(f"wail:tenant:{tenant_id}")
        return TenantCredentials.model_validate_json(raw) if raw else None

    async def save(self, creds):
        await redis.set(f"wail:tenant:{creds.tenant_id}", creds.model_dump_json())

    async def delete(self, tenant_id):
        await redis.delete(f"wail:tenant:{tenant_id}")
```

---

## Building a new Connector

> A "connector" is a **Connection** + the **Integration** that sits on top of it.
> Follow this guide to add support for any new external service.

### Step 1 — Create the Connection

Create `backend/connections/my_service.py`. Subclass the appropriate ABC:

- Use `AbstractCalendarConnection` if your service is a calendar.
- Use `AbstractPaymentConnection` if your service is a payment provider.
- Use `AbstractConnection` for anything else (CRM, ecommerce, inventory, …).

```python
# backend/connections/my_service.py
from __future__ import annotations
from typing import ClassVar
from connections.base import AbstractConnection

class MyServiceConnection(AbstractConnection):
    name:         ClassVar[str] = "my_service"
    display_name: ClassVar[str] = "My Service"

    def __init__(self) -> None:
        # Load credentials — check _tenant_ctx first for multi-tenant support,
        # fall back to CredentialStore for single-tenant backward compatibility.
        from tenant import _tenant_ctx
        from credentials import credential_store
        ctx = _tenant_ctx.get()
        self._api_key = (ctx.extra.get("my_service_api_key") if ctx
                         else credential_store.get("MY_SERVICE_API_KEY"))

    @property
    def connected(self) -> bool:
        # IMPORTANT: re-check the context var here — do NOT cache to self.
        # This property is called once per request; the ContextVar holds the
        # per-request credentials so each tenant sees their own state.
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        key = ctx.extra.get("my_service_api_key", "") if ctx else self._api_key
        return bool(key)

    def disconnect(self) -> None:
        from credentials import credential_store
        credential_store.clear("MY_SERVICE_API_KEY")

    def fetch_contacts(self, query: str) -> list[dict]:
        if not self.connected:
            raise RuntimeError("My Service is not connected.")
        # ... call the API ...
        return []
```

**The `connected` property and ContextVars**

`connected` must check `_tenant_ctx.get()` each time it is called, not cache the result on `self`. In multi-tenant mode, the same connection *object* is shared across all tenants — only the ContextVar changes per request. Storing credentials directly on `self` would be a data leak.

### Step 2 — Create the Integration

Create `backend/integrations/my_service.py`. Subclass `AbstractIntegration`:

```python
# backend/integrations/my_service.py
from __future__ import annotations
from typing import Callable, ClassVar
from integrations.base import AbstractIntegration, _make_http_helpers
from connections.my_service import MyServiceConnection

class MyServiceIntegration(AbstractIntegration):
    name:         ClassVar[str] = "my_service"
    display_name: ClassVar[str] = "My Service"
    description:  ClassVar[str] = "Search and manage My Service contacts."

    def __init__(self, connection: MyServiceConnection | None = None) -> None:
        self.connection = connection or MyServiceConnection()

    def get_manifest_extras(self) -> dict:
        return {"connected": self.connection.connected}

    def get_settings_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "My Service API key",
                },
            },
        }

    def get_mcp_tools(self, wail_base: str, api_key: str) -> list[Callable]:
        _get, _post, _delete = _make_http_helpers(wail_base, api_key)

        def search_contacts(query: str) -> dict:
            """Search contacts by name or email.

            Args:
                query: The search term.
            """
            return _get(f"/my-service/contacts?q={query}")

        return [search_contacts]
```

**Rules for MCP tools**
- Must be regular (non-async) functions.
- Must have a descriptive docstring — FastMCP surfaces it to the agent.
- All arguments and return values must be JSON-serialisable.
- Use `_make_http_helpers()` to call back to the WAIL REST API — don't import connections directly, since the MCP server runs in a separate process.

### Step 3 — Wire it into WailApp

Open `backend/integrations/registry.py` and add your integration to `WailApp.__init__`:

```python
# In WailApp.__init__, after the existing integrations:
try:
    from connections.my_service import MyServiceConnection
    from integrations.my_service import MyServiceIntegration
    self.my_service_conn        = MyServiceConnection()
    self.my_service_integration = MyServiceIntegration(self.my_service_conn)
except ImportError:
    self.my_service_conn        = None
    self.my_service_integration = None
```

And add it to `ALL_INTEGRATIONS`:

```python
@property
def ALL_INTEGRATIONS(self) -> list:
    built_ins = [
        self.calendar_integration,
        self.booking_integration,
        self.payment_integration,
        self.my_service_integration,   # ← add here
    ]
    return [i for i in built_ins if i is not None] + self._extra_integrations
```

Add a module-level alias at the bottom of the file:

```python
my_service_integration = wail.my_service_integration
```

### Step 4 — Add HTTP routes (optional)

If your integration needs REST endpoints (for webhooks, credential management, etc.), create `backend/routers/my_service.py`:

```python
from fastapi import APIRouter
from integrations.registry import my_service_integration

router = APIRouter(prefix="/my-service", tags=["my_service"])

@router.get("/contacts")
async def search_contacts(q: str = ""):
    return my_service_integration.connection.fetch_contacts(q)
```

Then include it in `main.py` (two lines):

```python
from routers.my_service import router as my_service_router
app.include_router(my_service_router)
```

### Step 5 — Test it

`wail-core[test]` provides pytest fixtures without any `conftest.py`:

```python
from wail_testing import WailTestClient, validate_plugin
from integrations.my_service import MyServiceIntegration

def test_contract():
    validate_plugin(MyServiceIntegration())

def test_appears_in_manifest(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[MyServiceIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        r = client.get("/integrations/manifest")
        names = [i["name"] for i in r.json()]
        assert "my_service" in names
```

---

## Publishing a connector as a plugin

If you want to distribute your connector independently (i.e. users install it with `pip install wail-plugin-myservice`), you don't need to modify core at all. See **[PLUGINS.md](PLUGINS.md)** for the full guide.

The short version: declare a `wail.plugins` entry point in your package's `pyproject.toml` and WAIL discovers and loads it automatically at startup:

```toml
[project.entry-points."wail.plugins"]
my_service = "my_service_integration:plugin_integration"
```

---

## CLI tools

| Command | What it does |
|---|---|
| `wail-serve` | Start the production API server |
| `wail-dev my_plugin.py` | Hot-reload sandbox with a built-in dashboard — no npm needed |
| `wail-create-plugin` | Interactive scaffolder: generates a complete plugin package with tests |

---

## Auth model

| Mode | Who can call it | Requires |
|---|---|---|
| **Observe** | Any agent | Nothing — publicly readable data (availability, hours, products) |
| **Interact** | Authorised agents only | API key set by the business owner via the dashboard |

---

## Project layout

```
wail/
├── backend/
│   ├── main.py                    # FastAPI app, middleware, router mounts
│   ├── wail.py                    # WAIL class (server entry point)
│   ├── tenant.py                  # Stateless multi-tenancy (ContextVar + TenantStore)
│   ├── credentials.py             # Single-tenant CredentialStore
│   ├── cache.py                   # Pluggable CacheStore
│   ├── registry.py                # APIRegistry (dynamic endpoint dispatch)
│   ├── shared_config.py           # Cross-integration settings (timezone, hours, …)
│   ├── wail_testing.py            # pytest fixtures, mocks, WailTestClient
│   ├── dev_sandbox.py             # wail-dev hot-reload sandbox
│   ├── create_plugin.py           # wail-create-plugin scaffolder
│   ├── connections/
│   │   ├── base.py                # AbstractConnection, AbstractCalendarConnection
│   │   ├── payment.py             # AbstractPaymentConnection
│   │   ├── google_calendar.py     # Google Calendar (requires wail-core[google])
│   │   ├── stripe_payment.py      # Stripe (requires wail-core[stripe])
│   │   └── oauth.py               # AbstractOAuthConnection helper
│   ├── integrations/
│   │   ├── base.py                # AbstractIntegration
│   │   ├── calendar.py            # CalendarIntegration
│   │   ├── booking.py             # BookingIntegration
│   │   ├── payment.py             # PaymentIntegration
│   │   └── registry.py            # WailApp (wires connections + integrations)
│   ├── routers/
│   │   ├── auth.py                # OAuth flow routes
│   │   ├── booking.py             # Booking API routes
│   │   ├── calendar.py            # Calendar API routes
│   │   ├── payment.py             # Payment/webhook routes
│   │   ├── plugins.py             # GET /integrations/manifest
│   │   ├── config.py              # Shared settings routes
│   │   ├── trust.py               # Trust/auth mode routes
│   │   └── deps.py                # FastAPI dependencies (require_admin, etc.)
│   └── protocols/
│       ├── mcp.py                 # MCP protocol adapter
│       └── openapi.py             # OpenAPI Actions adapter
└── frontend/
    └── app/
        └── dashboard/
            └── integrations/      # Dynamic integration config UI
```

---

## License

MIT
