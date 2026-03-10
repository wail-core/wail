"""
Trust / access control for externally-facing write endpoints.

Every agent-callable endpoint that mutates data runs is_trusted() before acting.
The check is a no-op when testing=true (the default), so nothing breaks during
development.

Config (trust_config.json):
  {
    "testing":   true,       -- bypass all checks (default for new installs)
    "mode":      "all",      -- "all" | "api_key" | "allowlist"
    "api_keys":  [],         -- opaque bearer tokens (api_key mode)
    "allowlist": []          -- contact emails / phone numbers (allowlist mode)
  }

api_key mode  — caller sends:  Authorization: Bearer <key>
                           or:  X-WAIL-Key: <key>

allowlist mode — the customer_contact value submitted with the request must
                 appear in the allowlist (case-insensitive).
"""

import json
import secrets
from pathlib import Path

from fastapi import Request

TRUST_CONFIG_PATH = Path(__file__).parent.parent / "trust_config.json"

DEFAULT_CONFIG: dict = {
    "testing":   True,
    "mode":      "all",
    "api_keys":  [],
    "allowlist": [],
}


# ── Persistence ───────────────────────────────────────────────────

def load_trust_config() -> dict:
    if not TRUST_CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(TRUST_CONFIG_PATH.read_text())
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_trust_config(config: dict) -> None:
    TRUST_CONFIG_PATH.write_text(json.dumps(config, indent=2))


# ── Key management ────────────────────────────────────────────────

def generate_api_key() -> str:
    """Return a new cryptographically random API key."""
    return secrets.token_urlsafe(32)


def add_api_key(config: dict) -> str:
    """Generate a key, add it to config in-place, return the new key."""
    key = generate_api_key()
    config.setdefault("api_keys", []).append(key)
    return key


def remove_api_key(config: dict, key: str) -> bool:
    keys = config.get("api_keys", [])
    if key in keys:
        keys.remove(key)
        return True
    return False


# ── Trust check ───────────────────────────────────────────────────

def is_trusted(request: Request, contact: str = "") -> bool:
    """
    Return True if the request is authorised to perform a write action.

    contact  — the customer_contact value from the request body; used in
               allowlist mode to verify the caller is a known contact.
    """
    config = load_trust_config()

    # Testing mode — everything passes
    if config.get("testing", True):
        return True

    mode = config.get("mode", "all")

    if mode == "all":
        return True

    if mode == "api_key":
        auth = request.headers.get("Authorization", "")
        key  = auth.removeprefix("Bearer ").strip()
        if not key:
            key = request.headers.get("X-WAIL-Key", "").strip()
        return key in config.get("api_keys", [])

    if mode == "allowlist":
        allowed = {c.lower().strip() for c in config.get("allowlist", [])}
        return contact.lower().strip() in allowed

    return False


def trust_error(config: dict) -> dict:
    """Return an appropriate error payload for a failed trust check."""
    if config.get("testing", True):
        return {"error": "Unauthorized"}  # shouldn't happen in testing mode
    mode = config.get("mode", "all")
    if mode == "api_key":
        return {
            "error": (
                "Unauthorized: a valid API key is required. "
                "Send it as  Authorization: Bearer <key>  or  X-WAIL-Key: <key>."
            )
        }
    if mode == "allowlist":
        return {
            "error": (
                "Unauthorized: your contact is not on the trusted list. "
                "Please contact the business to be added."
            )
        }
    return {"error": "Unauthorized"}
