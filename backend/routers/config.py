"""
Shared business config and integration management routes.

GET  /config                        — shared config (business name, timezone, hours, notifications)
POST /config                        — update shared config (admin)
GET  /config/schema                 — JSON Schema for dashboard form rendering

GET  /integrations                  — list all integrations + enabled status
POST /integrations/{name}/enable    — enable an integration (admin)
POST /integrations/{name}/disable   — disable an integration (admin)
GET  /integrations/{name}/schema    — JSON Schema for an integration's own settings
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from integrations.registry import ALL_INTEGRATIONS
from routers.deps import require_admin
from shared_config import SHARED_CONFIG_SCHEMA, load_shared_config, save_shared_config

router = APIRouter(tags=["Config"])


# ── Shared business config ─────────────────────────────────────────────────────

@router.get("/config")
async def get_shared_config():
    return load_shared_config()


@router.post("/config", dependencies=[Depends(require_admin)])
async def set_shared_config(request: Request):
    config = await request.json()
    save_shared_config(config)
    return {"status": "saved"}


@router.get("/config/schema")
async def get_shared_config_schema():
    return SHARED_CONFIG_SCHEMA


# ── Integration management ────────────────────────────────────────────────────

@router.get("/integrations")
async def list_integrations():
    return {
        "integrations": [
            {
                "name":         i.name,
                "display_name": i.display_name,
                "description":  i.description,
                "enabled":      i.is_enabled(),
            }
            for i in ALL_INTEGRATIONS
        ]
    }


@router.post("/integrations/{name}/enable", dependencies=[Depends(require_admin)])
async def enable_integration(name: str):
    integration = next((i for i in ALL_INTEGRATIONS if i.name == name), None)
    if not integration:
        return JSONResponse({"error": f"Integration '{name}' not found"}, status_code=404)
    integration.set_enabled(True)
    return {"status": "enabled", "name": name}


@router.post("/integrations/{name}/disable", dependencies=[Depends(require_admin)])
async def disable_integration(name: str):
    integration = next((i for i in ALL_INTEGRATIONS if i.name == name), None)
    if not integration:
        return JSONResponse({"error": f"Integration '{name}' not found"}, status_code=404)
    integration.set_enabled(False)
    return {"status": "disabled", "name": name}


@router.get("/integrations/{name}/schema")
async def integration_schema(name: str):
    integration = next((i for i in ALL_INTEGRATIONS if i.name == name), None)
    if not integration:
        return JSONResponse({"error": f"Integration '{name}' not found"}, status_code=404)
    return integration.get_settings_schema()
