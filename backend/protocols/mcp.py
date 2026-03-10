"""
MCP protocol adapter — exposes WAIL integrations as MCP tools.

Used by mcp_server.py (the entry-point script).  To add this protocol to a
new WAIL deployment, just run mcp_server.py and point your MCP client at it.

The adapter asks each integration for its MCP tools via get_mcp_tools()
and registers them all with FastMCP automatically.  Adding a new integration
to the list in mcp_server.py is all it takes to expose it here.

Transport: stdio (Claude Desktop compatible).
"""

from __future__ import annotations

from typing import ClassVar, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from protocols.base import AbstractProtocolAdapter

if TYPE_CHECKING:
    from integrations.base import AbstractIntegration


_INSTRUCTIONS = (
    "You are connected to a WAIL (Website Agent Integration Layer) server. "
    "WAIL gives you structured access to a business's booking system and calendar.\n\n"
    "Typical booking workflow:\n"
    "1. Call get_booking_config to learn available services and required fields.\n"
    "2. Call get_available_slots for the desired date and service.\n"
    "3. Call book_appointment with the chosen slot and all required fields.\n"
    "4. To cancel, call cancel_booking with the event_id and the contact value "
    "used when booking.\n\n"
    "Use get_worker_groups to discover workers and pass the group name to "
    "get_available_slots or book_appointment to target a specific worker."
)


class MCPAdapter(AbstractProtocolAdapter):
    """
    Builds a FastMCP server from all registered integrations.

    Each integration's get_mcp_tools(wail_base, api_key) is called;
    the returned callables are registered as MCP tools automatically.

    Args:
        wail_base: Base URL of the running WAIL backend.
        api_key:   Bearer key for api_key trust mode; empty in testing mode.
    """

    name:         ClassVar[str] = "mcp"
    display_name: ClassVar[str] = "MCP (Model Context Protocol)"

    def __init__(
        self,
        wail_base: str = "http://localhost:8000",
        api_key:   str = "",
    ) -> None:
        self.wail_base = wail_base.rstrip("/")
        self.api_key   = api_key

    def build(self, integrations: list[AbstractIntegration]) -> FastMCP:
        """
        Create a FastMCP instance with tools sourced from every integration.

        To expose a new integration via MCP:
        1.  Implement get_mcp_tools() in the integration class.
        2.  Add the integration singleton to the list in mcp_server.py.
        No changes to this adapter are needed.
        """
        mcp = FastMCP("WAIL", instructions=_INSTRUCTIONS)

        for integration in integrations:
            for tool_fn in integration.get_mcp_tools(self.wail_base, self.api_key):
                mcp.tool()(tool_fn)

        return mcp
