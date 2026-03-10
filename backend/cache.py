"""
WAIL Cache Layer

Three modes per entry:

  mode="static"  — value only changes when you call set() or refresh() manually.
                   Nothing happens in the background. Good for config-like data.

  mode="poll"    — a background asyncio task calls refresher() every
                   interval_seconds. WAIL owns the fetch cadence.
                   Agents always read a fresh value without triggering a real fetch.

  mode="push"    — an external program is responsible for keeping the value
                   current by calling push() (same process) or
                   POST /_wail/cache/push/{key} (HTTP, cross-process).
                   WAIL trusts whatever was last pushed; no polling is done.
                   Good when the source system already emits change events
                   (webhooks, POS callbacks, booking confirmations, etc.).

Binding:
  registry.register(..., bind="my_key") wires an endpoint's output directly
  to a cache entry. No handler needed — the endpoint just returns
  cache_store.get("my_key") on every call.

Pluggable backends:
  CacheStore accepts a BaseCacheBackend for value storage. The default is
  InMemoryCacheBackend. To use Redis, implement BaseCacheBackend and pass
  it to CacheStore:

      from cache import CacheStore
      store = CacheStore(backend=RedisCacheBackend("redis://localhost:6379"))
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

CacheMode = Literal["static", "poll", "push"]


# ── Storage backend ───────────────────────────────────────────────────────────

class BaseCacheBackend(ABC):
    """
    Pluggable storage backend for cache values.

    Implement this to store cache values somewhere other than in-process
    memory (e.g. Redis for multi-instance deployments).

    The backend only stores values — polling intervals, refresh callbacks,
    and the mode logic live in CacheStore, not here.
    """

    @abstractmethod
    def get(self, key: str) -> Any:
        """Return the stored value for key, or None if not set."""
        ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """Store a value for key."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove a key."""
        ...


class InMemoryCacheBackend(BaseCacheBackend):
    """Default backend — stores values in a plain dict. Does not survive restarts."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


# ── Cache entry metadata ──────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    key:              str
    mode:             CacheMode
    interval_seconds: float         # poll only; ignored for static/push
    refresher:        Callable | None  # poll/static only; ignored for push
    last_updated:     float | None = None
    _task:            asyncio.Task | None = field(default=None, repr=False, compare=False)


# ── Cache store ───────────────────────────────────────────────────────────────

class CacheStore:
    def __init__(self, backend: BaseCacheBackend | None = None) -> None:
        self._backend = backend or InMemoryCacheBackend()
        self._entries: dict[str, CacheEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        key: str,
        *,
        mode: CacheMode = "static",
        refresher: Callable | None = None,
        interval_seconds: float = 30.0,
        initial_value: Any = None,
    ) -> CacheEntry:
        """
        Register a cache entry.

        Args:
            key:              Unique name for this cache variable.
            mode:             "static" | "poll" | "push"  (see module docstring).
            refresher:        Callable returning the fresh value.
                              Required for mode="poll"; optional for mode="static"
                              (enables on-demand refresh()); ignored for mode="push".
            interval_seconds: How often to auto-refresh (mode="poll" only).
            initial_value:    Seed value before the first refresh fires.
        """
        if mode == "poll" and refresher is None:
            raise ValueError(f"Cache entry '{key}' with mode='poll' requires a refresher.")
        entry = CacheEntry(
            key=key,
            mode=mode,
            interval_seconds=interval_seconds,
            refresher=refresher,
            last_updated=time.time() if initial_value is not None else None,
        )
        self._entries[key] = entry
        if initial_value is not None:
            self._backend.set(key, initial_value)
        return entry

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, key: str) -> dict:
        """
        Return the current value plus cache metadata.
        'data' is the payload agents consume.
        '_cache' is metadata for transparency / debugging.
        """
        entry = self._entries.get(key)
        if entry is None:
            return {"error": f"No cache entry registered for key '{key}'"}
        return {
            "data": self._backend.get(key),
            "_cache": {
                "key": key,
                "mode": entry.mode,
                "last_updated": entry.last_updated,
                "refresh_interval_seconds": (
                    entry.interval_seconds if entry.mode == "poll" else None
                ),
            },
        }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any) -> bool:
        """
        Overwrite a cache entry directly (any mode).
        Use for static entries or emergency overrides.
        """
        entry = self._entries.get(key)
        if entry is None:
            return False
        self._backend.set(key, value)
        entry.last_updated = time.time()
        return True

    def push(self, key: str, value: Any) -> bool:
        """
        Accept an update from an external program (mode="push" entries).
        The caller is asserting that this value is the current truth —
        WAIL will serve it as-is until the next push arrives.

        Also accepted for mode="static" entries (manual override).
        Raises ValueError for mode="poll" entries (WAIL owns those fetches).
        """
        entry = self._entries.get(key)
        if entry is None:
            return False
        if entry.mode == "poll":
            raise ValueError(
                f"Cache entry '{key}' is mode='poll' — WAIL owns its refresh cycle. "
                "Use set() to force-override, or switch the entry to mode='push'."
            )
        self._backend.set(key, value)
        entry.last_updated = time.time()
        return True

    def refresh(self, key: str) -> bool:
        """
        Manually trigger a synchronous refresh for entries that have a refresher.
        Works for mode="static" and mode="poll"; not applicable to mode="push".
        """
        entry = self._entries.get(key)
        if entry is None or entry.refresher is None:
            return False
        if entry.mode == "push":
            raise ValueError(
                f"Cache entry '{key}' is mode='push' — external programs own its updates. "
                "Call push() or POST /_wail/cache/push/{key} instead."
            )
        value = entry.refresher()
        self._backend.set(key, value)
        entry.last_updated = time.time()
        return True

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def info(self, key: str) -> dict | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        return {
            "key": entry.key,
            "mode": entry.mode,
            "last_updated": entry.last_updated,
            "refresh_interval_seconds": (
                entry.interval_seconds if entry.mode == "poll" else None
            ),
            "has_refresher": entry.refresher is not None,
        }

    def list_all(self) -> list[dict]:
        return [self.info(k) for k in self._entries]

    # ------------------------------------------------------------------
    # Lifecycle — called from FastAPI lifespan
    # ------------------------------------------------------------------

    def start(self):
        """Start background poll loops for all mode='poll' entries."""
        for key, entry in self._entries.items():
            if entry.mode == "poll" and entry.refresher:
                # Seed immediately before starting the interval loop
                if not asyncio.iscoroutinefunction(entry.refresher):
                    self._backend.set(key, entry.refresher())
                    entry.last_updated = time.time()
                entry._task = asyncio.create_task(self._poll_loop(entry))

    def stop(self):
        """Cancel all background tasks on app shutdown."""
        for entry in self._entries.values():
            if entry._task and not entry._task.done():
                entry._task.cancel()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _poll_loop(self, entry: CacheEntry):
        while True:
            await asyncio.sleep(entry.interval_seconds)
            try:
                if asyncio.iscoroutinefunction(entry.refresher):
                    value = await entry.refresher()
                else:
                    value = entry.refresher()
                self._backend.set(entry.key, value)
                entry.last_updated = time.time()
            except Exception:
                pass  # keep stale value on error; don't crash the loop


# No module-level singleton — create a CacheStore via WAIL():
#
#     from wail import WAIL
#     wail = WAIL()
#     # wail.cache is a fully wired CacheStore
#
# To use a custom backend:
#     from my_redis_backend import RedisCacheBackend
#     wail = WAIL(cache_backend=RedisCacheBackend("redis://localhost:6379"))
