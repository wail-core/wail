"""
WAIL Core — the central application object.

A developer building on WAIL creates one instance and mounts their routers on it:

    from wail import WAIL
    from fastapi.middleware.cors import CORSMiddleware

    wail = WAIL()
    app  = wail.app          # expose to uvicorn: uvicorn main:app

    app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
    app.include_router(my_booking_router)

    # Run directly (development):
    if __name__ == "__main__":
        wail.serve()

Custom cache backend (e.g. Redis for multi-instance deployments):

    from cache import CacheStore
    from my_redis_backend import RedisCacheBackend

    wail = WAIL(cache_backend=RedisCacheBackend("redis://localhost:6379"))

Custom connection (e.g. swap Stripe for Paddle):

    from integrations.registry import WailApp
    from my_connectors import PaddlePaymentConnection

    wail = WAIL(app=WailApp(payment_conn=PaddlePaymentConnection()))
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cache import BaseCacheBackend, CacheStore
from registry import APIRegistry

if TYPE_CHECKING:
    from integrations.registry import WailApp as _WailApp


class WAIL:
    """
    Central WAIL application object.

    Everything that was previously a module-level singleton (registry, cache)
    lives here as an instance variable. This means you can run multiple isolated
    WAIL instances in the same Python process — useful for tests, multi-tenancy
    setups, and staging vs production environments.

    Args:
        cache_backend:  Custom BaseCacheBackend implementation.
                        Defaults to InMemoryCacheBackend (in-process only).
                        Pass a Redis backend for multi-instance deployments.
        app_instance:   Custom WailApp instance (connections + integrations).
                        Defaults to the standard WailApp with Google Calendar
                        and Stripe. Pass a customised WailApp to swap connectors.
    """

    def __init__(
        self,
        cache_backend:  BaseCacheBackend | None = None,
        app_instance:   "_WailApp | None"       = None,
    ) -> None:
        # ── Core components ───────────────────────────────────────────────────
        self.cache    = CacheStore(backend=cache_backend)
        self.registry = APIRegistry(cache=self.cache)

        # ── FastAPI with lifecycle wired to this instance's cache ─────────────
        @asynccontextmanager
        async def _lifespan(fastapi_app: FastAPI):
            self.cache.start()   # start poll loops
            yield
            self.cache.stop()    # cancel background tasks on shutdown

        self.app = FastAPI(
            title="WAIL API",
            docs_url=None,
            redoc_url=None,
            lifespan=_lifespan,
        )

        # ── Mount the three built-in WAIL routes ──────────────────────────────
        self._setup_routes()

    # ── Built-in routes ───────────────────────────────────────────────────────

    def _setup_routes(self) -> None:
        """
        Wire the three core WAIL routes onto self.app.

        These are framework routes — every WAIL deployment gets them.
        Integration-specific routes (booking, calendar, etc.) are added by the
        caller via app.include_router(...).
        """

        @self.app.get("/")
        async def root():
            """Self-describing manifest — lists all registered endpoints."""
            return self.registry.manifest()

        @self.app.get("/_wail/openapi.json")
        async def openapi_spec():
            """
            OpenAPI 3.1 spec dynamically generated from the current registry state.

            Paste this URL into a ChatGPT Action import or any OpenAPI-aware
            client — it always reflects the live set of registered endpoints.
            """
            from protocols.openapi import build_openapi_spec
            return build_openapi_spec(self.registry)

        @self.app.post("/_wail/cache/push/{key}")
        async def cache_push(key: str, request: Request):
            """
            Cross-process cache push (mode='push' entries only).

            External programs call this instead of reaching into the in-process
            cache directly. The body is the new value — WAIL serves it verbatim
            until the next push arrives.
            """
            body = await request.json()
            try:
                ok = self.cache.push(key, body)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            if not ok:
                return JSONResponse(
                    {"error": f"No cache entry registered for key '{key}'"},
                    status_code=404,
                )
            return JSONResponse({"status": "ok", "key": key})

        @self.app.api_route(
            "/{full_path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        )
        async def dispatcher(request: Request, full_path: str):
            """
            Dynamic dispatcher — routes requests to handlers registered via
            wail.registry.register(). Falls back to 404 if the path is unknown.

            FastAPI's own routers (include_router) take priority because they are
            matched first. The dispatcher only handles paths not claimed by a router.
            """
            path = f"/{full_path}"
            ep   = self.registry.get(path, request.method)

            if not ep:
                return JSONResponse(
                    {"error": f"No endpoint registered at {request.method} {path}"},
                    status_code=404,
                )

            sig    = inspect.signature(ep.handler)
            kwargs = {"request": request} if "request" in sig.parameters else {}
            result = (
                await ep.handler(**kwargs)
                if inspect.iscoroutinefunction(ep.handler)
                else ep.handler(**kwargs)
            )
            return JSONResponse(result)

    # ── Convenience ───────────────────────────────────────────────────────────

    def serve(self, host: str = "0.0.0.0", port: int = 8000, **uvicorn_kwargs) -> None:
        """
        Start the uvicorn server.

        For production, prefer running uvicorn directly so you can control
        worker count and other process settings:

            uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
        """
        try:
            import uvicorn
        except ImportError:
            raise RuntimeError("uvicorn not installed. Run: pip install uvicorn")
        uvicorn.run(self.app, host=host, port=port, **uvicorn_kwargs)
