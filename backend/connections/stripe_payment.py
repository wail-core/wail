"""
Stripe payment connection — link-handoff via Stripe Checkout.

Credentials are set via the admin API and stored in credentials.json:
    STRIPE_SECRET_KEY      — Stripe secret API key (sk_live_... or sk_test_...)
    STRIPE_WEBHOOK_SECRET  — Webhook signing secret from Stripe dashboard (whsec_...)

Falls back to environment variables on first run (migration path for existing .env setups).

The webhook endpoint must be registered in the Stripe dashboard pointing at:
    POST https://<your-wail-domain>/integrations/payment/webhook
    Events to listen for: checkout.session.completed

The full booking payload is stored in the Stripe session metadata so that no
intermediate database is needed — the webhook reconstructs everything from it.

Metadata size limit
-------------------
Stripe allows up to 500 characters per metadata value.  The booking payload
is serialised as JSON into metadata["payload"].  Typical bookings are well
under 300 characters; very long notes may cause a PaymentIntegration error
before the session is created.
"""

from __future__ import annotations

from typing import ClassVar

from connections.payment import AbstractPaymentConnection, CheckoutSession, SessionStatus
from credentials import credential_store as _default_credential_store, CredentialStore


class StripePaymentConnection(AbstractPaymentConnection):
    """
    Stripe Checkout adapter (mode="payment", hosted page).

    Credentials are stored as instance variables and persisted to
    credentials.json via CredentialStore. Call set_credentials() to
    configure at runtime; no server restart required.

    Args:
        credential_store: Injected CredentialStore. Defaults to the module-level
                          singleton (single-tenant). Pass a per-tenant instance for
                          multi-tenant deployments.

    To configure:
        POST /integrations/payment/credentials
        { "secret_key": "sk_test_...", "webhook_secret": "whsec_..." }
    """

    name:         ClassVar[str] = "stripe"
    display_name: ClassVar[str] = "Stripe"

    def __init__(self, credential_store: "CredentialStore | None" = None) -> None:
        self._cred_store     = credential_store or _default_credential_store
        # Fallback credentials for single-tenant mode (no TenantMiddleware).
        self._secret_key     = self._cred_store.get("STRIPE_SECRET_KEY")
        self._webhook_secret = self._cred_store.get("STRIPE_WEBHOOK_SECRET")

    # ── Context-aware credential resolution ──────────────────────────────────

    def _resolve_secret_key(self) -> str:
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        return (ctx.stripe_secret_key if ctx and ctx.stripe_secret_key
                else self._secret_key)

    def _resolve_webhook_secret(self) -> str:
        from tenant import _tenant_ctx
        ctx = _tenant_ctx.get()
        return (ctx.stripe_webhook_secret if ctx and ctx.stripe_webhook_secret
                else self._webhook_secret)

    # ── Credential management ─────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return bool(self._resolve_secret_key())

    def set_credentials(self, secret_key: str, webhook_secret: str = "") -> None:
        """
        Update Stripe credentials on this instance and persist to credentials.json.

        Takes effect immediately — no server restart required.
        """
        self._secret_key     = secret_key
        self._webhook_secret = webhook_secret
        self._cred_store.set(
            STRIPE_SECRET_KEY=secret_key,
            STRIPE_WEBHOOK_SECRET=webhook_secret,
        )

    def disconnect(self) -> None:
        """Remove Stripe credentials from memory and from credentials.json."""
        self._secret_key     = ""
        self._webhook_secret = ""
        self._cred_store.clear("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")

    def get_webhook_secret(self) -> str:
        return self._resolve_webhook_secret()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _stripe(self):
        """Return the stripe module with the API key set."""
        try:
            import stripe as _stripe
        except ImportError:
            raise RuntimeError(
                "stripe package not installed. Run: pip install stripe"
            )
        key = self._resolve_secret_key()
        if not key:
            raise RuntimeError(
                "Stripe secret key not configured. "
                "POST /integrations/payment/credentials first."
            )
        _stripe.api_key = key
        return _stripe

    # ── AbstractPaymentConnection implementation ──────────────────────────────

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
        stripe = self._stripe()

        kwargs: dict = {
            "mode": "payment",
            "line_items": [{
                "price_data": {
                    "currency":     currency,
                    "product_data": {"name": description},
                    "unit_amount":  amount_cents,
                },
                "quantity": 1,
            }],
            # Truncate values to Stripe's 500-char limit as a safety net
            "metadata":    {k: str(v)[:500] for k, v in metadata.items()},
            "success_url": success_url,
            "cancel_url":  cancel_url,
        }
        if customer_email:
            kwargs["customer_email"] = customer_email

        session = stripe.checkout.Session.create(**kwargs)

        return CheckoutSession(
            session_id  = session.id,
            payment_url = session.url,
            expires_at  = str(session.expires_at) if getattr(session, "expires_at", None) else None,
        )

    def get_session_status(self, session_id: str) -> SessionStatus:
        stripe  = self._stripe()
        session = stripe.checkout.Session.retrieve(session_id)
        details = session.customer_details

        email = ""
        name  = ""
        if details:
            email = getattr(details, "email", "") or ""
            name  = getattr(details, "name",  "") or ""

        return SessionStatus(
            session_id     = session.id,
            status         = session.status,
            payment_status = session.payment_status,
            metadata       = dict(session.metadata or {}),
            customer_email = email,
            customer_name  = name,
        )

    def verify_webhook(
        self,
        payload_bytes: bytes,
        signature:     str,
        secret:        str,
    ) -> dict:
        stripe = self._stripe()
        try:
            event = stripe.Webhook.construct_event(payload_bytes, signature, secret)
        except Exception as e:
            raise ValueError(f"Invalid Stripe webhook signature: {e}")
        return dict(event)
