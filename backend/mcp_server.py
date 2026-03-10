"""
WAIL MCP Server — entry point.

Runs the MCPAdapter with all registered integrations over stdio transport,
making WAIL's capabilities available to Claude Desktop and other MCP clients.

Setup
-----
1. Install dependencies:
       pip install mcp httpx

2. Add to your MCP client config.  For Claude Desktop
   (~/Library/Application Support/Claude/claude_desktop_config.json):

       {
         "mcpServers": {
           "wail": {
             "command": "python",
             "args": ["/absolute/path/to/wail/backend/mcp_server.py"],
             "env": {
               "WAIL_URL": "http://localhost:8000"
             }
           }
         }
       }

Environment variables
---------------------
WAIL_URL      Base URL of the WAIL backend  (default: http://localhost:8000)
WAIL_API_KEY  Bearer key for api_key trust mode  (leave empty in testing mode)

Adding a new integration
------------------------
Import its singleton from integrations.registry and append it to the list
passed to adapter.serve().  No changes to protocols/mcp.py are needed.
"""

import os

from protocols.mcp import MCPAdapter
from integrations.registry import calendar_integration, booking_integration, payment_integration

WAIL_BASE = os.environ.get("WAIL_URL", "http://localhost:8000")
WAIL_KEY  = os.environ.get("WAIL_API_KEY", "")

if __name__ == "__main__":
    adapter = MCPAdapter(wail_base=WAIL_BASE, api_key=WAIL_KEY)
    adapter.serve([calendar_integration, booking_integration, payment_integration])
