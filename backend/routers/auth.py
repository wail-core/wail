"""
Google OAuth routes — /auth/google and /auth/google/callback.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from integrations.registry import google_calendar_conn
from routers.deps import require_admin

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/google", dependencies=[Depends(require_admin)])
async def auth_google():
    try:
        url = google_calendar_conn.get_auth_url()
        return RedirectResponse(url)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/google/callback")
async def auth_google_callback(code: str):
    google_calendar_conn.exchange_code(code)
    return RedirectResponse("http://localhost:3000/dashboard/calendar?connected=true")
