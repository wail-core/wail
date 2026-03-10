"""
Plugin / integration discovery routes.

GET /integrations/manifest  — machine-readable list of every registered
                              integration, used by the dashboard to build
                              its UI without hardcoding integration names.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/integrations", tags=["Plugins"])


@router.get("/manifest")
async def integrations_manifest():
    """
    Return metadata for every registered integration.

    The dashboard calls this on page load and renders one card per entry.
    Plugin packages that inject integrations via WailApp(extra_integrations=[...])
    appear here automatically — no frontend changes needed.

    Response shape::

        [
          {
            "name":           "calendar",
            "display_name":   "Calendar",
            "description":    "...",
            "enabled":        true,
            "settings_schema": { "type": "object", "properties": { ... } },
            "connected":      true,       # present when integration has a connection
            "connect_url":    "/auth/google"  # present when OAuth flow exists
          },
          ...
        ]
    """
    # Late import avoids circular dependency at module load time.
    from integrations.registry import ALL_INTEGRATIONS

    result = []
    for integration in ALL_INTEGRATIONS:
        entry: dict = {
            "name":            integration.name,
            "display_name":    integration.display_name,
            "description":     integration.description,
            "enabled":         integration.is_enabled(),
            "settings_schema": integration.get_settings_schema(),
        }
        entry.update(integration.get_manifest_extras())
        result.append(entry)

    return result
