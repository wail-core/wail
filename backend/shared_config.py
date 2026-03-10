"""
Shared business configuration — accessible by all integrations.

Settings here are cross-cutting: any integration (booking, calendar, GHL, etc.)
can read or write them via GET/POST /config.  Integration-specific settings
stay in each integration's own config file.

Shared settings
---------------
business_name  — displayed in agent responses and confirmation messages
timezone       — IANA timezone used by all integrations (e.g. 'America/New_York')
hours          — weekly business schedule; null means closed that day
notifications  — global controls for booking confirmations and alerts

Config file: shared_config.json (next to backend root)
"""

import json
from pathlib import Path

SHARED_CONFIG_PATH = Path(__file__).parent / "shared_config.json"

DEFAULT_SHARED_CONFIG: dict = {
    "business_name": "",
    "timezone": "UTC",
    "hours": {
        "monday":    {"open": "09:00", "close": "19:00"},
        "tuesday":   {"open": "09:00", "close": "19:00"},
        "wednesday": {"open": "09:00", "close": "19:00"},
        "thursday":  {"open": "09:00", "close": "20:00"},
        "friday":    {"open": "09:00", "close": "20:00"},
        "saturday":  {"open": "08:00", "close": "18:00"},
        "sunday":    None,
    },
    "notifications": {
        "enabled": True,
    },
}

# JSON Schema — used by the dashboard to render the shared config form.
SHARED_CONFIG_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "business_name": {
            "type": "string",
            "description": "Business name shown in agent responses and booking confirmations.",
        },
        "timezone": {
            "type": "string",
            "description": (
                "IANA timezone for all integrations, e.g. 'America/New_York', 'Europe/Amsterdam'. "
                "All booking slots, calendar views, and agent responses use this timezone. "
                "Changing it affects how events are displayed and when slots are available."
            ),
        },
        "hours": {
            "type": "object",
            "description": (
                "Weekly business schedule used by booking, calendar views, and any other integration "
                "that needs operating hours. Set a day to null to mark it as closed. "
                "Times are in 24-hour HH:MM format in the configured timezone."
            ),
            "properties": {
                "monday":    {"$ref": "#/$defs/day_hours"},
                "tuesday":   {"$ref": "#/$defs/day_hours"},
                "wednesday": {"$ref": "#/$defs/day_hours"},
                "thursday":  {"$ref": "#/$defs/day_hours"},
                "friday":    {"$ref": "#/$defs/day_hours"},
                "saturday":  {"$ref": "#/$defs/day_hours"},
                "sunday":    {"$ref": "#/$defs/day_hours"},
            },
            "$defs": {
                "day_hours": {
                    "oneOf": [
                        {
                            "type": "null",
                            "description": "Closed (no slots offered on this day).",
                        },
                        {
                            "type": "object",
                            "required": ["open", "close"],
                            "properties": {
                                "open": {
                                    "type": "string",
                                    "pattern": "^\\d{2}:\\d{2}$",
                                    "description": "Opening time in 24h format, e.g. '09:00'.",
                                },
                                "close": {
                                    "type": "string",
                                    "pattern": "^\\d{2}:\\d{2}$",
                                    "description": "Closing time in 24h format, e.g. '19:00'.",
                                },
                            },
                        },
                    ],
                },
            },
        },
        "notifications": {
            "type": "object",
            "description": (
                "Global notification settings. Controls whether booking confirmations "
                "and other messages are included in responses."
            ),
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": (
                        "When true, a confirmation message is returned on every successful booking. "
                        "Set to false if your CRM or external system handles its own confirmations "
                        "and you don't want duplicate messages."
                    ),
                },
            },
        },
    },
}


def load_shared_config() -> dict:
    if not SHARED_CONFIG_PATH.exists():
        return dict(DEFAULT_SHARED_CONFIG)
    try:
        return json.loads(SHARED_CONFIG_PATH.read_text())
    except Exception:
        return dict(DEFAULT_SHARED_CONFIG)


def save_shared_config(config: dict) -> None:
    SHARED_CONFIG_PATH.write_text(json.dumps(config, indent=2))
