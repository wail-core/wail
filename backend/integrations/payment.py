"""
Payment integration — link-handoff payment orchestration.

This integration is connection-agnostic: it talks to any AbstractPaymentConnection
(Stripe today, Paddle/PayPal/Square tomorrow) and dispatches post-payment actions
to any registered handler.

Link-handoff flow
-----------------
1.  An HTTP endpoint calls payment_integration.create_payment_session(
        action="create_booking", payload={...booking data...}, ...
    ).
2.  WAIL returns {"status": "payment_required", "payment_url": "https://..."}.
3.  The agent presents the URL to the user.
4.  The user completes payment on the provider's hosted page.
5.  The provider POSTs a signed webhook to POST /integrations/payment/webhook.
6.  handle_webhook() verifies the signature, reads action from session metadata,
    and dispatches to the registered handler.
7.  The handler (e.g. booking_integration.create_booking) runs and confirms.

Action registration
-------------------
Any integration can register a post-payment action in integrations/registry.py:

    payment_integration.register_action(
        "create_booking",
        lambda payload: booking_integration.create_booking(
            payload["date"], payload["time"], payload["service_id"],
            payload, group=payload.get("group"),
        ),
    )

The payload is whatever was passed to create_payment_session — it is stored in
the provider's session metadata and returned to the handler verbatim.

Config file  (payment_config.json next to backend root):
    {
      "enabled":     false,
      "connection":  "stripe",
      "currency":    "usd",
      "success_url": "http://localhost:3000/booking/success?session_id={CHECKOUT_SESSION_ID}",
      "cancel_url":  "http://localhost:3000/booking/cancel",
      "customer_fields": {
        "email_field": "email",
        "name_field":  "name"
      }
    }

customer_fields maps payment-provider concepts to booking field IDs:
  email_field — which booking field holds the customer's email address
  name_field  — which booking field holds the customer's name
These are used to pre-fill the Stripe Checkout page so customers don't
have to re-enter info they already provided in the booking form.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from connections.payment import AbstractPaymentConnection
from integrations.base import AbstractIntegration, _make_http_helpers

PAYMENT_CONFIG_PATH = Path(__file__).parent.parent / "payment_config.json"

DEFAULT_CONFIG: dict = {
    "enabled":    False,
    "connection": "stripe",
    "currency":   "usd",
    "success_url": "http://localhost:3000/booking/success?session_id={CHECKOUT_SESSION_ID}",
    "cancel_url":  "http://localhost:3000/booking/cancel",
    "customer_fields": {
        "email_field": "email",
        "name_field":  "name",
    },
}


# ── Config persistence ────────────────────────────────────────────────────────

def load_payment_config() -> dict:
    if not PAYMENT_CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(PAYMENT_CONFIG_PATH.read_text())
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_payment_config(config: dict) -> None:
    PAYMENT_CONFIG_PATH.write_text(json.dumps(config, indent=2))


# ── Integration ───────────────────────────────────────────────────────────────

class PaymentIntegration(AbstractIntegration):
    """
    Orchestrates link-handoff payments over any AbstractPaymentConnection.

    Swap the payment provider by changing the connection singleton in
    integrations/registry.py — no changes here are needed.
    """

    name:         str = "payment"
    display_name: str = "Payment"
    description:  str = (
        "Link-handoff payments: create a checkout session, receive webhook "
        "confirmation, and trigger a post-payment action (e.g. confirm a booking)."
    )

    def __init__(self, connection: AbstractPaymentConnection) -> None:
        self.connection = connection
        self._action_handlers: dict[str, Callable[[dict], dict]] = {}

    def get_manifest_extras(self) -> dict:
        return {"connected": self.connection.connected}

    # ── Action registration ───────────────────────────────────────────────────

    def register_action(
        self,
        action_name: str,
        handler:     Callable[[dict], dict],
    ) -> None:
        """
        Register a handler that runs when a webhook confirms a payment whose
        session metadata["action"] matches action_name.

        Args:
            action_name: Unique string identifying the action, e.g. "create_booking".
            handler:     Callable that receives the stored payload dict and returns
                         a result dict.  Exceptions are caught and reported.

        Call this in integrations/registry.py after all singletons are created:

            payment_integration.register_action(
                "create_booking",
                lambda p: booking_integration.create_booking(
                    p["date"], p["time"], p["service_id"], p,
                    group=p.get("group"),
                ),
            )
        """
        self._action_handlers[action_name] = handler

    # ── Config ────────────────────────────────────────────────────────────────

    def load_config(self) -> dict:
        return load_payment_config()

    def save_config(self, config: dict) -> None:
        save_payment_config(config)

    # ── Session creation ──────────────────────────────────────────────────────

    def create_payment_session(
        self,
        amount_cents:          int,
        description:           str,
        action:                str,
        payload:               dict,
        customer_fields_from:  dict,
    ) -> dict:
        """
        Create a checkout session and return the payment URL.

        The entire `payload` is serialised into the session metadata under the
        key "payload" and passed back to the action handler after payment
        completes.  Keep it small — Stripe caps metadata values at 500 chars.

        Args:
            amount_cents:         Amount in smallest currency unit (e.g. cents).
            description:          Line-item description, e.g. "Haircut on 2024-03-15 at 10:00".
            action:               Action name registered with register_action(),
                                  e.g. "create_booking".
            payload:              Arbitrary dict stored in session metadata and
                                  passed to the handler on webhook confirmation.
            customer_fields_from: Dict to extract the customer email and name from,
                                  using the field mappings in payment config.

        Returns:
            {
                "status":       "payment_required",
                "session_id":   "cs_...",
                "payment_url":  "https://checkout.stripe.com/...",
                "expires_at":   "...",   # ISO 8601 or null
                "amount_cents": 2500,
                "currency":     "usd",
                "description":  "...",
            }

        Raises:
            RuntimeError: If the payment connection is not configured.
            ValueError:   If the payload JSON exceeds 490 characters.
        """
        if not self.connection.connected:
            raise RuntimeError(
                "Payment connection not configured. "
                "POST /integrations/payment/credentials first."
            )

        config      = self.load_config()
        currency    = config.get("currency", "usd")
        success_url = config.get("success_url", DEFAULT_CONFIG["success_url"])
        cancel_url  = config.get("cancel_url",  DEFAULT_CONFIG["cancel_url"])
        field_map   = config.get("customer_fields", DEFAULT_CONFIG["customer_fields"])

        customer_email = (customer_fields_from.get(field_map.get("email_field", "email")) or "").strip()
        customer_name  = (customer_fields_from.get(field_map.get("name_field",  "name"))  or "").strip()

        payload_json = json.dumps(payload)
        if len(payload_json) > 490:
            raise ValueError(
                f"Booking payload is too large for payment metadata "
                f"({len(payload_json)} chars, max 490). "
                "Shorten field values or reduce the number of fields."
            )

        session = self.connection.create_checkout_session(
            amount_cents   = amount_cents,
            currency       = currency,
            description    = description,
            metadata       = {"action": action, "payload": payload_json},
            success_url    = success_url,
            cancel_url     = cancel_url,
            customer_email = customer_email,
            customer_name  = customer_name,
        )

        return {
            "status":       "payment_required",
            "session_id":   session.session_id,
            "payment_url":  session.payment_url,
            "expires_at":   session.expires_at,
            "amount_cents": amount_cents,
            "currency":     currency,
            "description":  description,
        }

    # ── Session status ────────────────────────────────────────────────────────

    def get_session_status(self, session_id: str) -> dict:
        """Retrieve the current status of a checkout session."""
        if not self.connection.connected:
            raise RuntimeError("Payment connection not configured.")
        s = self.connection.get_session_status(session_id)
        return {
            "session_id":     s.session_id,
            "status":         s.status,
            "payment_status": s.payment_status,
            "customer_email": s.customer_email,
            "customer_name":  s.customer_name,
        }

    # ── Webhook handling ──────────────────────────────────────────────────────

    def handle_webhook(self, payload_bytes: bytes, signature: str) -> dict:
        """
        Authenticate the provider webhook, extract the action, and dispatch.

        Returns:
            On a handled payment:
                {"status": "payment_confirmed", "action": "...", "result": {...}}
            On an action handler error (slot gone, etc.):
                {"status": "action_failed", "action": "...", "error": "...", "note": "..."}
            On an unhandled event type or missing action:
                {"status": "ignored", ...}

        Raises:
            ValueError: If the webhook signature is invalid.
        """
        secret = self.connection.get_webhook_secret()
        event  = self.connection.verify_webhook(payload_bytes, signature, secret)

        ev_type = event.get("type", "")
        if ev_type != "checkout.session.completed":
            return {"status": "ignored", "event_type": ev_type}

        session  = event["data"]["object"]
        metadata = session.get("metadata") or {}
        action   = metadata.get("action", "")

        payload: dict = {}
        try:
            payload = json.loads(metadata.get("payload", "{}"))
        except json.JSONDecodeError:
            pass

        if not action:
            return {"status": "ignored", "reason": "no action in session metadata"}

        handler = self._action_handlers.get(action)
        if not handler:
            return {"status": "ignored", "reason": f"no handler registered for action '{action}'"}

        try:
            result = handler(payload)
            return {"status": "payment_confirmed", "action": action, "result": result}
        except Exception as e:
            # Payment succeeded but the post-payment action failed (e.g. slot
            # became unavailable). Log this — manual resolution may be needed.
            return {
                "status": "action_failed",
                "action": action,
                "error":  str(e),
                "note":   (
                    "Payment was received but the post-payment action failed. "
                    "The customer's payment was collected. Manual intervention may be required."
                ),
            }

    # ── AbstractIntegration implementation ────────────────────────────────────

    def get_settings_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Require payment before confirming bookings.",
                },
                "connection": {
                    "type": "string",
                    "enum": ["stripe"],
                    "description": "Payment provider to use.",
                },
                "currency": {
                    "type": "string",
                    "description": "ISO 4217 currency code, e.g. 'usd', 'eur', 'gbp'.",
                },
                "success_url": {
                    "type": "string",
                    "description": (
                        "Redirect URL after successful payment. "
                        "Use {CHECKOUT_SESSION_ID} as a placeholder for the session ID."
                    ),
                },
                "cancel_url": {
                    "type": "string",
                    "description": "Redirect URL if the customer cancels payment.",
                },
                "customer_fields": {
                    "type": "object",
                    "description": (
                        "Maps payment provider concepts to booking field IDs, "
                        "so customer details collected in the booking form are "
                        "pre-filled on the payment page."
                    ),
                    "properties": {
                        "email_field": {
                            "type": "string",
                            "description": "Booking field ID whose value is used as the customer's email on the payment page.",
                        },
                        "name_field": {
                            "type": "string",
                            "description": "Booking field ID whose value is used as the customer's name on the payment page.",
                        },
                    },
                },
            },
        }

    def get_mcp_tools(self, wail_base: str, api_key: str) -> list:
        """Expose payment status tools to MCP agents."""
        _get, _, _ = _make_http_helpers(wail_base, api_key)

        def get_payment_config() -> dict:
            """
            Get the payment configuration — whether payments are required
            and which currency is used.

            Check this before booking to know whether the booking will return
            a payment_url that the customer must visit before the appointment
            is confirmed.
            """
            return _get("/integrations/payment/config")

        def check_payment_status(session_id: str) -> dict:
            """
            Check the status of a pending payment session.

            Args:
                session_id: The session_id returned when a booking initiated payment.

            Returns the payment status:
                "open"     — customer has not paid yet
                "complete" — paid; appointment has been confirmed
                "expired"  — session expired without payment
            """
            return _get(f"/integrations/payment/status/{session_id}")

        return [get_payment_config, check_payment_status]
