"""
Google Calendar routes — connection management, events, groups, calendar list.

Prefix: /integrations/google-calendar
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from integrations.registry import google_calendar_conn, calendar_integration
from routers.deps import require_admin, require_integration_enabled

_calendar_enabled = [Depends(require_integration_enabled("calendar"))]

router = APIRouter(prefix="/integrations/google-calendar", tags=["Google Calendar"])


# ── Connection management ──────────────────────────────────────────────────────

@router.get("/credentials")
async def get_credentials():
    return {
        "configured": google_calendar_conn.configured,
    }


@router.post("/credentials", dependencies=[Depends(require_admin)])
async def set_credentials(request: Request):
    body          = await request.json()
    client_id     = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        return JSONResponse({"error": "client_id and client_secret are required"}, status_code=400)
    google_calendar_conn.set_credentials(client_id, client_secret)
    return {"status": "saved"}


@router.get("/status")
async def status():
    return {"connected": google_calendar_conn.connected}


@router.delete("", dependencies=[Depends(require_admin)])
async def disconnect():
    google_calendar_conn.disconnect()
    return {"status": "disconnected"}


# ── Events ────────────────────────────────────────────────────────────────────

@router.post("/events", dependencies=_calendar_enabled)
async def get_events(request: Request):
    settings = await request.json()
    try:
        events = calendar_integration.fetch_events(settings)
        return {"events": events, "count": len(events)}
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/events/{group}", dependencies=_calendar_enabled)
async def get_events_for_group(group: str, request: Request):
    settings = await request.json()
    try:
        events = calendar_integration.fetch_events_for_group(group, settings)
        return {"events": events, "count": len(events), "group": group}
    except (RuntimeError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Groups (workers) ──────────────────────────────────────────────────────────

@router.get("/groups", dependencies=_calendar_enabled)
async def get_groups():
    return {"groups": calendar_integration.get_groups()}


@router.post("/groups", dependencies=[Depends(require_admin), *_calendar_enabled])
async def set_groups(request: Request):
    body   = await request.json()
    groups = body.get("groups", [])
    calendar_integration.save_groups(groups)
    return {"status": "saved", "count": len(groups)}


# ── Calendar list ─────────────────────────────────────────────────────────────

@router.get("/calendars", dependencies=_calendar_enabled)
async def list_calendars():
    if not google_calendar_conn.connected:
        return JSONResponse({"error": "Google Calendar not connected"}, status_code=400)
    try:
        return {"calendars": calendar_integration.list_calendars()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
