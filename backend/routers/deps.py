"""
Shared FastAPI dependencies.

Import require_admin in any router that has admin-only endpoints:

    from routers.deps import require_admin
    router = APIRouter()

    @router.post("/something", dependencies=[Depends(require_admin)])
    async def protected_endpoint(): ...
"""

import os
import secrets

from fastapi import HTTPException, Request


def require_integration_enabled(integration_name: str):
    """
    Returns a dependency that blocks the route with 503 if the named
    integration is currently disabled.

    Apply at router level to gate every route in the router:
        router = APIRouter(dependencies=[Depends(require_integration_enabled("booking"))])

    Apply at route level to gate a single endpoint:
        @router.get("/foo", dependencies=[Depends(require_integration_enabled("payment"))])
    """
    def check():
        # Late import avoids a circular dependency at module load time.
        from integrations.registry import ALL_INTEGRATIONS
        integration = next((i for i in ALL_INTEGRATIONS if i.name == integration_name), None)
        if integration is not None and not integration.is_enabled():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": f"The '{integration_name}' integration is currently disabled.",
                    "integration": integration_name,
                    "enabled": False,
                    "hint": f"POST /integrations/{integration_name}/enable to re-enable it.",
                },
            )
    return check


def require_admin(request: Request) -> None:
    """
    Enforce admin authentication via the X-WAIL-Admin-Key header.

    If WAIL_ADMIN_KEY is not set in the environment the check is skipped
    (development mode).  In production always set a strong random value.

    secrets.compare_digest is used to prevent timing-attack enumeration.
    """
    admin_key = os.environ.get("WAIL_ADMIN_KEY", "").strip()
    if not admin_key:
        return  # dev mode — open
    provided = request.headers.get("X-WAIL-Admin-Key", "").strip()
    if not provided or not secrets.compare_digest(provided, admin_key):
        raise HTTPException(
            status_code=401,
            detail=(
                "Admin key required. Send it as the X-WAIL-Admin-Key header. "
                "Set WAIL_ADMIN_KEY in the server environment to enable admin auth."
            ),
        )
