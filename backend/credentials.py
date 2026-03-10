"""
Credential store — persists connection credentials to credentials.json.

All connection credentials flow through here rather than the .env file.
The .env file is for static config (ports, app settings). This file is
for runtime secrets set via the admin API (Stripe keys, Google OAuth, etc.).

Priority: credentials.json > environment variables.
This means a credential set via POST /credentials always takes effect
immediately, even if the same key exists in .env.

credentials.json is gitignored. Never commit it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"


class CredentialStore:
    """
    Simple key-value store backed by a JSON file.

    Values set via set() are written to disk immediately and survive server
    restarts. Falls back to os.environ for any key not in the JSON file,
    so existing .env-based setups work without migration.

    Usage:
        from credentials import credential_store

        # Read
        key = credential_store.get("STRIPE_SECRET_KEY")

        # Write (persists to credentials.json)
        credential_store.set(STRIPE_SECRET_KEY="sk_live_...", STRIPE_WEBHOOK_SECRET="whsec_...")

        # Remove (reverts to env var fallback, or empty string)
        credential_store.clear("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
    """

    def __init__(self, path: Path = CREDENTIALS_PATH) -> None:
        self._path = path

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        """
        Return the stored credential for key.

        Checks credentials.json first, then os.environ, then returns default.
        """
        data = self._load()
        if key in data:
            return str(data[key])
        return os.environ.get(key, default)

    def set(self, **kwargs: str) -> None:
        """
        Store credentials. Accepts keyword arguments so you can set multiple
        keys in one call:

            credential_store.set(STRIPE_SECRET_KEY="sk_...", STRIPE_WEBHOOK_SECRET="whsec_...")
        """
        data = self._load()
        data.update({k: str(v) for k, v in kwargs.items()})
        self._save(data)

    def clear(self, *keys: str) -> None:
        """
        Remove credentials from credentials.json.

        After clearing, get() will fall back to os.environ (or return "").
        Does NOT remove anything from .env or os.environ.
        """
        data = self._load()
        changed = False
        for key in keys:
            if key in data:
                del data[key]
                changed = True
        if changed:
            self._save(data)

    def all(self) -> dict:
        """Return all credentials stored in the JSON file (not env vars)."""
        return dict(self._load())


# Module-level singleton — import this from connections
credential_store = CredentialStore()
