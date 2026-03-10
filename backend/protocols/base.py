"""
Abstract base for all WAIL protocol adapters.

A "protocol adapter" translates WAIL's capabilities into a specific
agent-facing protocol: MCP (Claude Desktop), OpenAPI Actions (ChatGPT),
Gemini Extensions, plain REST manifest, etc.

Hierarchy
---------
AbstractProtocolAdapter          ← implement this for every new protocol
    └── MCPAdapter               (Model Context Protocol — stdio transport)

To add a new protocol adapter (e.g. OpenAPI / ChatGPT Actions):
1.  Subclass AbstractProtocolAdapter.
2.  Set name and display_name class variables.
3.  Implement build() — it receives all registered integrations and must
    return a server object (with a .run() method) or any other value.
4.  Override serve() if your adapter doesn't use .run() (e.g. file output).
5.  Add an entry point script (like mcp_server.py) that calls adapter.serve().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.base import AbstractIntegration


class AbstractProtocolAdapter(ABC):
    """
    Translates WAIL integrations into a specific agent-facing protocol.

    The adapter receives a list of AbstractIntegration instances and is
    responsible for collecting their capabilities and presenting them in
    the target protocol's format.

    Flow:
        integrations = [calendar_integration, booking_integration, ...]
        adapter = MyAdapter(...)
        adapter.serve(integrations)   # build + run

    See protocols/mcp.py for a complete worked example.
    """

    name:         ClassVar[str]  # machine slug,  e.g. "mcp"
    display_name: ClassVar[str]  # human label,   e.g. "MCP (Claude Desktop)"

    @abstractmethod
    def build(self, integrations: list[AbstractIntegration]) -> Any:
        """
        Build the protocol-specific server or specification.

        Args:
            integrations: All AbstractIntegration instances to expose.

        Returns:
            A server object (FastMCP, Starlette app, …) or a spec dict.
            The default serve() implementation calls .run() on the return value.

        Example (MCP):
            def build(self, integrations):
                mcp = FastMCP("WAIL")
                for integration in integrations:
                    for tool_fn in integration.get_mcp_tools(self.wail_base, self.api_key):
                        mcp.tool()(tool_fn)
                return mcp

        Example (OpenAPI spec — file output, no .run()):
            def build(self, integrations):
                return {"openapi": "3.1.0", "paths": { ... }}

            def serve(self, integrations):
                spec = self.build(integrations)
                Path("openapi.json").write_text(json.dumps(spec, indent=2))
                print("Wrote openapi.json")
        """
        ...

    def serve(self, integrations: list[AbstractIntegration]) -> None:
        """
        Build the server and start serving.

        The default implementation calls build() then .run() on the result,
        which covers stdio-transport MCP servers and similar patterns.

        Override for adapters that produce file output, HTTP servers with
        custom startup logic, or anything that doesn't expose .run().
        """
        server = self.build(integrations)
        server.run()
