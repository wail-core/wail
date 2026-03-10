from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class EndpointDef:
    path: str
    method: str
    description: str
    handler: Callable[..., Any]
    tags: list[str] = field(default_factory=list)


class APIRegistry:
    """
    Dynamic endpoint registry. Add, remove, and update endpoints at runtime.
    The main dispatcher reads from this registry on every request.

    Args:
        cache: CacheStore instance used by the bind= feature.
               Injected automatically when created via WAIL().
               Required if you call register(..., bind=...).
    """

    def __init__(self, cache=None):
        self._endpoints: dict[tuple[str, str], EndpointDef] = {}
        self._cache = cache  # set by WAIL; used by bind=

    def register(
        self,
        path: str,
        description: str,
        handler: Callable | None = None,
        method: str = "GET",
        tags: list[str] | None = None,
        bind: str | None = None,
    ) -> EndpointDef:
        """
        Register an endpoint. Overwrites if already exists.

        Args:
            bind: Cache key to bind this endpoint to. When set, the endpoint's
                  response is always the current value of cache_store.get(bind).
                  Mutually exclusive with handler.
        """
        if bind is not None:
            if self._cache is None:
                raise ValueError(
                    f"Cannot use bind='{bind}' without a cache. "
                    "Create APIRegistry via WAIL() so the cache is wired automatically."
                )
            _cache = self._cache
            _key   = bind
            handler = lambda: _cache.get(_key)
        if handler is None:
            raise ValueError("Either handler or bind must be provided.")
        key = (method.upper(), path)
        ep = EndpointDef(
            path=path,
            method=method.upper(),
            description=description,
            handler=handler,
            tags=tags or [],
        )
        self._endpoints[key] = ep
        return ep

    def unregister(self, path: str, method: str = "GET") -> bool:
        """Remove an endpoint. Returns True if it existed."""
        return self._endpoints.pop((method.upper(), path), None) is not None

    def update(self, path: str, method: str = "GET", **kwargs) -> EndpointDef | None:
        """Update fields on an existing endpoint definition."""
        ep = self._endpoints.get((method.upper(), path))
        if not ep:
            return None
        for k, v in kwargs.items():
            if hasattr(ep, k):
                setattr(ep, k, v)
        return ep

    def get(self, path: str, method: str = "GET") -> EndpointDef | None:
        return self._endpoints.get((method.upper(), path))

    def list_all(self) -> list[EndpointDef]:
        return list(self._endpoints.values())

    def manifest(self) -> dict:
        """Root-level self-description returned at GET /."""
        return {
            "service": "WAIL API",
            "version": "0.1.0",
            "description": "Website Agent Integration Layer — registry-based API gateway for AI agents.",
            "endpoints": [
                {
                    "path": ep.path,
                    "method": ep.method,
                    "description": ep.description,
                    "tags": ep.tags,
                }
                for ep in self._endpoints.values()
            ],
        }

