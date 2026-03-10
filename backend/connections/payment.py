"""
Abstract base for WAIL payment connections.

A payment connection handles link-handoff payments: WAIL creates a checkout
session, the customer pays on the provider's hosted page, and the provider
sends a webhook back to WAIL when payment is complete.

Hierarchy
---------
AbstractPaymentConnection        ← implement this for any payment provider
    └── StripePaymentConnection  (Stripe Checkout — hosted page)

Data models (Pydantic)
----------------------
CheckoutSession and SessionStatus are Pydantic models.  Values returned by
a payment connection are validated on construction — type errors in a
third-party connector are caught before they reach the integration layer.

To add a new payment provider (Paddle, PayPal, Square, …):
1.  Subclass AbstractPaymentConnection.
2.  Set name / display_name class variables.
3.  Implement create_checkout_session, get_session_status, verify_webhook.
4.  Wire up the singleton in integrations/registry.py.

Metadata contract
-----------------
Whatever is passed as `metadata` to create_checkout_session must be preserved
exactly and returned in SessionStatus.metadata and in the verify_webhook
event payload. WAIL uses this to store the post-payment action and its data:

    metadata["action"]  — action name, e.g. "create_booking"
    metadata["payload"] — JSON-encoded payload for that action

Providers must not mutate, drop, or truncate these keys.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from connections.base import AbstractConnection


# ── Shared data models (Pydantic) ─────────────────────────────────────────────

class CheckoutSession(BaseModel):
    """A hosted checkout session returned after session creation."""

    session_id:  str
    payment_url: str
    expires_at:  str | None = None   # ISO 8601 string or None


class SessionStatus(BaseModel):
    """Current state of a checkout session."""

    session_id:     str
    status:         str   # "open" | "complete" | "expired"
    payment_status: str   # "unpaid" | "paid" | "no_payment_required"
    metadata:       dict[str, Any] = Field(default_factory=dict)
    customer_email: str = ""
    customer_name:  str = ""


# ── Abstract interface ────────────────────────────────────────────────────────

class AbstractPaymentConnection(AbstractConnection):
    """
    Implement this class for each external payment provider.

    All implementations follow the link-handoff pattern:
    1.  WAIL calls create_checkout_session with amount + action metadata.
    2.  The session URL is returned to the agent → shown to the user.
    3.  The provider calls WAIL's webhook when payment completes.
    4.  WAIL calls verify_webhook to authenticate the event.
    5.  WAIL dispatches the post-payment action (e.g. create_booking).
    """

    @abstractmethod
    def create_checkout_session(
        self,
        amount_cents:   int,
        currency:       str,
        description:    str,
        metadata:       dict,
        success_url:    str,
        cancel_url:     str,
        customer_email: str = "",
        customer_name:  str = "",
    ) -> CheckoutSession:
        """
        Create a hosted checkout session and return its URL.

        Args:
            amount_cents:   Amount in smallest currency unit (e.g. cents for USD).
            currency:       ISO 4217 code, e.g. "usd".
            description:    Line-item description shown on the checkout page.
            metadata:       Key-value pairs preserved by the provider.
                            Must include "action" and "payload" for WAIL dispatch.
                            Values must be strings; max 500 chars each.
            success_url:    Redirect URL on successful payment.
            cancel_url:     Redirect URL if the customer cancels.
            customer_email: Pre-fill customer email (optional).
            customer_name:  Pre-fill customer name (optional).
        """
        ...

    @abstractmethod
    def get_session_status(self, session_id: str) -> SessionStatus:
        """Retrieve the current status of a checkout session."""
        ...

    @abstractmethod
    def verify_webhook(
        self,
        payload_bytes: bytes,
        signature:     str,
        secret:        str,
    ) -> dict:
        """
        Authenticate and parse a provider webhook payload.

        Args:
            payload_bytes: Raw request body bytes (must not be decoded first).
            signature:     Provider-specific signature header value.
            secret:        Webhook signing secret from the provider dashboard.

        Returns:
            Parsed event dict. Must expose:
                event["type"]                       — e.g. "checkout.session.completed"
                event["data"]["object"]["metadata"] — metadata dict from the session
                event["data"]["object"]["id"]       — session_id

        Raises:
            ValueError: If the signature is invalid or the payload is malformed.
        """
        ...

    def get_webhook_secret(self) -> str:
        """
        Return the webhook signing secret for this connection.

        Override in subclasses that store the webhook secret separately
        from the connection credentials (e.g. Stripe).
        Default: empty string (PaymentIntegration falls back to config).
        """
        return ""
