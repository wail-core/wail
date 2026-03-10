"""
OpenAPI 3.1 protocol adapter — dynamically generates a fully compliant
openapi.json spec from the WAIL registry.

Two ways to use this:

1. HTTP endpoint (dynamic, recommended):
   WAIL mounts GET /_wail/openapi.json automatically. Paste that URL into
   a ChatGPT Action schema import and ChatGPT will always see the current spec.

2. File output (static, for CDN or version-control workflows):
       from wail import WAIL
       from protocols.openapi import OpenAPIAdapter

       wail    = WAIL()
       adapter = OpenAPIAdapter(wail.registry, servers=[{"url": "https://api.example.com"}])
       adapter.serve()          # writes openapi.json to the current directory

Spec rules
----------
- Conforms to OpenAPI 3.1.0.
- Path parameters are detected automatically from {braces} in paths.
- POST / PUT / PATCH operations get a generic JSON request body.
- Response schema is {"type": "object"} — WAIL handlers return arbitrary dicts.
- WAIL-internal routes (/, /_wail/…) are included by default; pass
  include_internal=False to omit them.
- Tags are passed through from EndpointDef.tags; the "_wail" tag is reserved
  for the internal routes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar, TYPE_CHECKING

from protocols.base import AbstractProtocolAdapter

if TYPE_CHECKING:
    from integrations.base import AbstractIntegration
    from registry import APIRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _path_parameters(path: str) -> list[dict]:
    """Return an OpenAPI `parameters` list for every {brace} in *path*."""
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for name in _PATH_PARAM_RE.findall(path)
    ]


def _operation_id(method: str, path: str) -> str:
    """Stable, URL-safe operation ID derived from method + path."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
    return f"{method.lower()}_{slug}" if slug else method.lower()


# ── Internal (built-in WAIL) route definitions ────────────────────────────────

_INTERNAL_PATHS: dict[str, dict] = {
    "/": {
        "get": {
            "summary": "WAIL manifest — self-describing list of all registered endpoints.",
            "operationId": "get_manifest",
            "tags": ["_wail"],
            "parameters": [],
            "responses": {
                "200": {
                    "description": "Manifest",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    },
    "/_wail/openapi.json": {
        "get": {
            "summary": "This OpenAPI spec, dynamically generated from the current registry state.",
            "operationId": "get_openapi_spec",
            "tags": ["_wail"],
            "parameters": [],
            "responses": {
                "200": {
                    "description": "OpenAPI 3.1 specification",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    },
    "/_wail/cache/push/{key}": {
        "post": {
            "summary": "Push a new value into a cache entry (mode='push' entries only).",
            "operationId": "post_wail_cache_push_key",
            "tags": ["_wail"],
            "parameters": [
                {
                    "name": "key",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "The registered cache key to update.",
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "description": "New value to store. Served verbatim until the next push.",
                        }
                    }
                },
            },
            "responses": {
                "200": {"description": "Push accepted"},
                "400": {"description": "Entry is not in push mode"},
                "404": {"description": "No cache entry registered for this key"},
            },
        }
    },
}


# ── Core spec builder ─────────────────────────────────────────────────────────

def build_openapi_spec(
    registry: "APIRegistry",
    *,
    title: str = "WAIL API",
    version: str = "0.1.0",
    description: str = (
        "Website Agent Integration Layer — registry-based API gateway for AI agents. "
        "Endpoints tagged 'interact' may modify state; confirm with the user before calling them."
    ),
    servers: list[dict] | None = None,
    include_internal: bool = True,
) -> dict:
    """
    Build a fully compliant OpenAPI 3.1.0 spec from *registry*.

    Args:
        registry:         The APIRegistry instance to read endpoints from.
        title:            API title in the `info` block.
        version:          API version string in the `info` block.
        description:      API-level description shown in ChatGPT / Swagger UI.
        servers:          List of OpenAPI server objects, e.g.
                          [{"url": "https://api.example.com"}].
                          Defaults to localhost:8000.
        include_internal: Include WAIL's own routes (/, /_wail/…) in the spec.
                          Set to False when you only want the registered endpoints.

    Returns:
        A dict that serialises to a valid openapi.json.
    """
    paths: dict[str, dict] = {}

    # ── Registry endpoints ────────────────────────────────────────────────────
    for ep in registry.list_all():
        params    = _path_parameters(ep.path)
        operation = {
            "summary":     ep.description,
            "operationId": _operation_id(ep.method, ep.path),
            "tags":        ep.tags or [],
            "parameters":  params,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {"schema": {"type": "object"}}
                    },
                },
                "404": {
                    "description": "Endpoint not found in registry",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"error": {"type": "string"}},
                                "required": ["error"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
            },
        }

        if ep.method in {"POST", "PUT", "PATCH"}:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object"}}
                },
            }

        paths.setdefault(ep.path, {})[ep.method.lower()] = operation

    # ── Internal routes ───────────────────────────────────────────────────────
    if include_internal:
        for path, methods in _INTERNAL_PATHS.items():
            # Don't overwrite a user-registered endpoint at the same path
            if path not in paths:
                paths[path] = methods

    return {
        "openapi": "3.1.0",
        "info": {
            "title":       title,
            "version":     version,
            "description": description,
        },
        "servers": servers or [{"url": "http://localhost:8000"}],
        "paths": paths,
    }


# ── Protocol adapter ──────────────────────────────────────────────────────────

class OpenAPIAdapter(AbstractProtocolAdapter):
    """
    Generates an OpenAPI 3.1 spec from the WAIL registry and writes it to a
    file. Useful for static deployments, CDN hosting, or committing the spec
    to version control.

    For a live HTTP endpoint, WAIL automatically mounts GET /_wail/openapi.json
    — no need to run this adapter unless you want file output.

    Args:
        registry:         APIRegistry to read from (usually wail.registry).
        title:            Passed to build_openapi_spec.
        version:          Passed to build_openapi_spec.
        description:      Passed to build_openapi_spec.
        servers:          Passed to build_openapi_spec.
        include_internal: Passed to build_openapi_spec.

    Example:
        wail    = WAIL()
        adapter = OpenAPIAdapter(
            wail.registry,
            servers=[{"url": "https://api.example.com"}],
        )
        adapter.serve()     # writes openapi.json
    """

    name:         ClassVar[str] = "openapi"
    display_name: ClassVar[str] = "OpenAPI 3.1 (ChatGPT Actions / Swagger)"

    def __init__(
        self,
        registry: "APIRegistry",
        *,
        title:            str         = "WAIL API",
        version:          str         = "0.1.0",
        description:      str         = "",
        servers:          list[dict] | None = None,
        include_internal: bool        = True,
    ) -> None:
        self.registry         = registry
        self.title            = title
        self.version          = version
        self.description      = description
        self.servers          = servers
        self.include_internal = include_internal

    # AbstractProtocolAdapter contract
    # integrations is accepted for interface compatibility but unused here —
    # OpenAPI speaks to the registry, not to integration objects.

    def build(self, integrations: "list[AbstractIntegration] | None" = None) -> dict:
        """Return the OpenAPI spec dict."""
        kwargs: dict = {
            "title":            self.title,
            "version":          self.version,
            "include_internal": self.include_internal,
        }
        if self.description:
            kwargs["description"] = self.description
        if self.servers:
            kwargs["servers"] = self.servers
        return build_openapi_spec(self.registry, **kwargs)

    def serve(
        self,
        integrations: "list[AbstractIntegration] | None" = None,
        output_path: str = "openapi.json",
    ) -> None:
        """
        Write the spec to *output_path* (default: ``openapi.json`` in the cwd).

        Unlike MCP or HTTP adapters, this adapter produces a file — there is no
        long-running process to start.
        """
        spec = self.build(integrations)
        Path(output_path).write_text(json.dumps(spec, indent=2))
        print(f"Wrote {output_path}  ({len(spec['paths'])} paths)")
