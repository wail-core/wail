"""
Trust / access control routes.

Prefix: /integrations/trust
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from integrations.trust import (
    add_api_key, load_trust_config, remove_api_key, save_trust_config,
)
from routers.deps import require_admin

router = APIRouter(prefix="/integrations/trust", tags=["Trust"])


@router.get("/config")
async def get_config():
    return load_trust_config()


@router.post("/config", dependencies=[Depends(require_admin)])
async def set_config(request: Request):
    body   = await request.json()
    config = load_trust_config()
    for key in ("testing", "mode", "allowlist"):
        if key in body:
            config[key] = body[key]
    save_trust_config(config)
    return {"status": "saved"}


@router.post("/api-keys", dependencies=[Depends(require_admin)])
async def generate_key():
    config = load_trust_config()
    key    = add_api_key(config)
    save_trust_config(config)
    return {"key": key}


@router.delete("/api-keys/{key}", dependencies=[Depends(require_admin)])
async def revoke_key(key: str):
    config  = load_trust_config()
    removed = remove_api_key(config, key)
    if not removed:
        return JSONResponse({"error": "Key not found"}, status_code=404)
    save_trust_config(config)
    return {"status": "revoked"}
