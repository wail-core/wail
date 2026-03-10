"""
Abstract base for all WAIL integrations.

An "integration" is a coherent set of capabilities (calendar viewing,
booking management, ecommerce, etc.) built on top of one or more Connections.
It owns business logic, configuration persistence, and protocol exposure.

Hierarchy
---------
AbstractIntegration              ← implement this for every new feature set
    ├── CalendarIntegration      (calendar events + worker groups)
    └── BookingIntegration       (availability, booking, cancellation)

To add a new integration (e.g. ecommerce):
1.  Subclass AbstractIntegration.
2.  Set name, display_name, description class variables.
3.  Accept the connection(s) you need in __init__.
4.  Implement get_settings_schema() so the dashboard can render a config form.
5.  Implement get_mcp_tools() to expose capabilities to MCP agents (optional).
6.  Wire up the singleton in integrations/registry.py.
7.  Add HTTP routes in main.py (FastAPI APIRouter recommended).
"""

from __future__ import annotations

import json
from abc import ABC
from pathlib import Path
from typing import Callable, ClassVar

# ── Integration state (enabled/disabled) ──────────────────────────────────────
#
# Tracks which integrations are currently active.  Stored separately from each
# integration's own config so the toggle survives config resets.

_STATE_PATH = Path(__file__).parent.parent / "integrations_state.json"


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.write_text(json.dumps(state, indent=2))


# ── HTTP helper factory ───────────────────────────────────────────────────────
#
# Used inside get_mcp_tools() implementations to talk back to the WAIL REST API.
# The MCP server runs as a separate process, so tools must use HTTP.

def _make_http_helpers(
    wail_base: str,
    api_key:   str,
) -> tuple[Callable, Callable, Callable]:
    """
    Return (_get, _post, _delete) helpers pre-configured for wail_base.

    Usage inside get_mcp_tools():
        _get, _post, _delete = _make_http_helpers(wail_base, api_key)
        def my_tool() -> dict:
            return _get("/my/endpoint")
    """
    import httpx

    headers: dict = {"X-WAIL-Key": api_key} if api_key else {}
    _connect_err = f"Cannot reach WAIL at {wail_base}. Is the backend running?"

    def _get(path: str) -> dict:
        try:
            with httpx.Client() as c:
                r = c.get(f"{wail_base}{path}", headers=headers, timeout=15)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError:
            return {"error": _connect_err}
        except Exception as e:
            return {"error": str(e)}

    def _post(path: str, body: dict) -> dict:
        try:
            with httpx.Client() as c:
                r = c.post(f"{wail_base}{path}", json=body, headers=headers, timeout=15)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError:
            return {"error": _connect_err}
        except Exception as e:
            return {"error": str(e)}

    def _delete(path: str, body: dict) -> dict:
        try:
            with httpx.Client() as c:
                r = c.delete(f"{wail_base}{path}", json=body, headers=headers, timeout=15)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError:
            return {"error": _connect_err}
        except Exception as e:
            return {"error": str(e)}

    return _get, _post, _delete


# ── Abstract base ─────────────────────────────────────────────────────────────

class AbstractIntegration(ABC):
    """
    Base class for all WAIL integrations.

    Subclasses must declare three class-level strings and may override
    get_settings_schema() and get_mcp_tools() to plug into the dashboard
    and protocol adapters automatically.
    """

    name:         ClassVar[str]  # machine slug,  e.g. "booking"
    display_name: ClassVar[str]  # human label,   e.g. "Booking"
    description:  ClassVar[str]  # one-liner for agents / manifest

    # ── Settings schema ───────────────────────────────────────────
    #
    # Return a JSON Schema dict describing every configurable option.
    # The dashboard uses this to render settings forms dynamically,
    # so keep property descriptions user-facing and accurate.

    def get_manifest_extras(self) -> dict:
        """
        Extra metadata to include in the GET /integrations/manifest response.

        Override to expose runtime state that the dashboard needs, such as
        whether the underlying connection is authenticated.

        Example return value (CalendarIntegration)::

            {
                "connected": True,
                "connect_url": "/auth/google",
            }

        Default: empty dict (no extras — integration has no connection state
        to report, or it inherits status from another integration).
        """
        return {}

    def get_settings_schema(self) -> dict:
        """
        JSON Schema for this integration's configuration.

        Override and return a full schema dict.  The default returns an
        empty object schema (integration has no user-configurable settings).

        Example return value:
            {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA timezone name"},
                    "enabled":  {"type": "boolean"},
                },
            }
        """
        return {"type": "object", "properties": {}}

    # ── MCP tool exposure ─────────────────────────────────────────
    #
    # Return a list of plain Python callables.  Each one becomes an MCP tool.
    # - Must be a regular (non-async) function.
    # - Must have a descriptive Google-style docstring — FastMCP exposes it.
    # - All arguments and return values must be JSON-serializable.
    # - Use _make_http_helpers() to talk back to the WAIL REST API.
    #
    # The MCPAdapter calls integration.get_mcp_tools(wail_base, api_key)
    # for every registered integration and registers all returned callables.

    def get_mcp_tools(self, wail_base: str, api_key: str) -> list[Callable]:
        """
        Return Python callables to expose as MCP tools.

        Override to expose your integration's capabilities to MCP agents.
        Default: no tools (integration not visible via MCP).

        Args:
            wail_base: Base URL of the running WAIL backend, e.g. "http://localhost:8000".
            api_key:   Bearer key for api_key trust mode; empty string in testing mode.

        Returns:
            List of callables.  Each will be registered with mcp.tool().

        Example:
            def get_mcp_tools(self, wail_base, api_key):
                _get, _post, _delete = _make_http_helpers(wail_base, api_key)

                def list_products() -> dict:
                    "List available products."
                    return _get("/products")

                return [list_products]
        """
        return []

    # ── Enable / disable ──────────────────────────────────────────
    #
    # Controls whether the integration's business logic layer is active.
    # This is separate from the connection (e.g. Google Calendar OAuth stays
    # connected even when the booking integration is disabled).

    def is_enabled(self) -> bool:
        """Return True if this integration is currently active (default: True)."""
        return _load_state().get(self.name, {}).get("enabled", True)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable this integration without touching its connection or config."""
        state = _load_state()
        state.setdefault(self.name, {})["enabled"] = enabled
        _save_state(state)
