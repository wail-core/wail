"""
Booking routes — config, slot availability, create and cancel appointments.

Prefix: /integrations/google-calendar/booking
"""

from datetime import date as Date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from integrations.booking import get_trust_contact, validate_booking_fields
from integrations.registry import (
    google_calendar_conn,
    booking_integration,
    stripe_payment_conn,
    payment_integration,
)
from integrations.trust import is_trusted, load_trust_config, trust_error
from routers.deps import require_admin, require_integration_enabled

router = APIRouter(
    prefix="/integrations/google-calendar/booking",
    tags=["Booking"],
    dependencies=[Depends(require_integration_enabled("booking"))],
)


# ── Config ────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    # Effective config merges shared settings (timezone, hours) with booking-specific
    # settings so agents see the full picture in a single call.
    return booking_integration.load_effective_config()


@router.post("/config", dependencies=[Depends(require_admin)])
async def set_config(request: Request):
    config = await request.json()
    booking_integration.save_config(config)
    return {"status": "saved"}


# ── Available slots ───────────────────────────────────────────────────────────

@router.post("/slots")
async def get_slots(request: Request):
    body       = await request.json()
    date_str   = body.get("date")
    service_id = body.get("service_id")
    group      = body.get("group")

    if not date_str or not service_id:
        return JSONResponse({"error": "date and service_id are required"}, status_code=400)

    config = booking_integration.load_effective_config()
    if not config.get("enabled", True):
        return JSONResponse({"error": "Booking is disabled"}, status_code=400)
    if not google_calendar_conn.connected:
        return JSONResponse({"error": "Google Calendar not connected"}, status_code=400)

    try:
        slots, config = booking_integration.get_available_slots(date_str, service_id, group=group)
        d       = Date.fromisoformat(date_str)
        service = next(s for s in config["services"] if s["id"] == service_id)
        return {
            "date":             date_str,
            "day":              d.strftime("%A"),
            "service_id":       service_id,
            "service_name":     service["name"],
            "duration_minutes": service["duration_minutes"],
            "group":            group,
            "available_slots":  slots,
            "count":            len(slots),
        }
    except (ValueError, StopIteration) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Create appointment ────────────────────────────────────────────────────────

@router.post("/book")
async def book(request: Request):
    body       = await request.json()
    date_str   = body.get("date")
    time_str   = body.get("time")
    service_id = body.get("service_id")
    group      = body.get("group")

    if not date_str or not time_str or not service_id:
        return JSONResponse({"error": "date, time, and service_id are required"}, status_code=400)

    config = booking_integration.load_effective_config()
    if not config.get("enabled", True):
        return JSONResponse({"error": "Booking is disabled"}, status_code=400)

    # Trust check — must happen before any calendar I/O or payment
    contact = get_trust_contact(body, config)
    if not is_trusted(request, contact):
        return JSONResponse(trust_error(load_trust_config()), status_code=403)

    if not google_calendar_conn.connected:
        return JSONResponse({"error": "Google Calendar not connected"}, status_code=400)

    # Payment gate: if payment is enabled and the service has a price,
    # create a checkout session instead of immediately booking.
    # The actual calendar event is created by the webhook handler once
    # payment is confirmed (POST /integrations/payment/webhook).
    payment_cfg = payment_integration.load_config()
    if payment_cfg.get("enabled") and stripe_payment_conn.connected:
        try:
            missing = validate_booking_fields(body, config)
            if missing:
                return JSONResponse(
                    {"error": f"Missing required fields: {', '.join(missing)}"},
                    status_code=400,
                )

            service = next(
                (s for s in config.get("services", []) if s["id"] == service_id), None
            )
            if not service:
                return JSONResponse(
                    {"error": f"Service '{service_id}' not found"}, status_code=400
                )

            amount_cents = int(float(service.get("price", 0)) * 100)
            if amount_cents > 0:
                slots, _ = booking_integration.get_available_slots(date_str, service_id, group=group)
                if time_str not in slots:
                    return JSONResponse({"error": f"{time_str} is not available."}, status_code=409)

                booking_payload = {
                    "date": date_str, "time": time_str,
                    "service_id": service_id, "group": group,
                    **body,
                }
                result = payment_integration.create_payment_session(
                    amount_cents         = amount_cents,
                    description          = f"{service['name']} on {date_str} at {time_str}",
                    action               = "create_booking",
                    payload              = booking_payload,
                    customer_fields_from = body,
                )
                return JSONResponse(result)

        except (ValueError, RuntimeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # No payment required — create the booking directly
    try:
        result = booking_integration.create_booking(date_str, time_str, service_id, body, group=group)
        return result
    except ValueError as e:
        msg    = str(e)
        status = 409 if "not available" in msg else 400
        return JSONResponse({"error": msg}, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Cancel appointment ────────────────────────────────────────────────────────

@router.delete("/book/{event_id}")
async def cancel(event_id: str, request: Request):
    body    = await request.json()
    contact = (body.get("contact") or "").strip()

    if not contact:
        return JSONResponse({"error": "contact is required to verify booking ownership"}, status_code=400)

    if not is_trusted(request, contact):
        return JSONResponse(trust_error(load_trust_config()), status_code=403)

    if not google_calendar_conn.connected:
        return JSONResponse({"error": "Google Calendar not connected"}, status_code=400)

    try:
        booking_integration.cancel_booking(event_id, contact)
        return {"status": "cancelled", "event_id": event_id}
    except LookupError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
