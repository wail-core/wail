"""
Payment routes — Stripe credentials, config, session status, webhook.

Prefix: /integrations/payment
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from integrations.registry import stripe_payment_conn, payment_integration
from routers.deps import require_admin, require_integration_enabled

_payment_enabled = [Depends(require_integration_enabled("payment"))]

router = APIRouter(prefix="/integrations/payment", tags=["Payment"])


# ── Credentials ───────────────────────────────────────────────────────────────

@router.get("/credentials", dependencies=[Depends(require_admin)])
async def get_credentials():
    return {
        "configured":         stripe_payment_conn.connected,
        "has_webhook_secret": bool(stripe_payment_conn.get_webhook_secret()),
        "provider":           stripe_payment_conn.display_name,
    }


@router.post("/credentials", dependencies=[Depends(require_admin)])
async def set_credentials(request: Request):
    body           = await request.json()
    secret_key     = (body.get("secret_key") or "").strip()
    webhook_secret = (body.get("webhook_secret") or "").strip()
    if not secret_key:
        return JSONResponse({"error": "secret_key is required"}, status_code=400)
    stripe_payment_conn.set_credentials(secret_key, webhook_secret)
    return {"status": "saved"}


@router.delete("/credentials", dependencies=[Depends(require_admin)])
async def disconnect():
    stripe_payment_conn.disconnect()
    return {"status": "disconnected"}


# ── Config ────────────────────────────────────────────────────────────────────

@router.get("/config", dependencies=_payment_enabled)
async def get_config():
    return payment_integration.load_config()


@router.post("/config", dependencies=[Depends(require_admin), *_payment_enabled])
async def set_config(request: Request):
    config = await request.json()
    payment_integration.save_config(config)
    return {"status": "saved"}


# ── Session status (agent polls after showing payment link) ───────────────────

@router.get("/status/{session_id}", dependencies=_payment_enabled)
async def session_status(session_id: str):
    if not stripe_payment_conn.connected:
        return JSONResponse({"error": "Payment not configured"}, status_code=400)
    try:
        return payment_integration.get_session_status(session_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Stripe webhook ────────────────────────────────────────────────────────────
# Stripe Dashboard → Webhooks → add endpoint pointing here.
# Events to subscribe to: checkout.session.completed

@router.post("/webhook", dependencies=_payment_enabled)
async def webhook(request: Request):
    payload_bytes = await request.body()
    signature     = request.headers.get("stripe-signature", "")
    try:
        result = payment_integration.handle_webhook(payload_bytes, signature)
        return JSONResponse(result)
    except ValueError as e:
        # Invalid signature — 400 tells Stripe to retry
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
