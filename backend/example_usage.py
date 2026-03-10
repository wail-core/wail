"""
Demo: WAIL serving a local barber shop — Tony's Cuts.

Shows how a developer builds on top of WAIL:
  1. Create a WAIL instance.
  2. Register cache entries on wail.cache.
  3. Register endpoints on wail.registry.
  4. Run the server.

Run with: python example_usage.py  (then visit http://localhost:8000)
"""

import random

from wail import WAIL

# ===========================================================================
# Create a WAIL instance
# ===========================================================================
#
# Everything (cache, registry, FastAPI app) lives on this object.
# No module-level singletons — you can create multiple isolated WAIL
# instances in the same process (useful for testing).

wail     = WAIL()
app      = wail.app
registry = wail.registry
cache    = wail.cache


# ===========================================================================
# Cache setup
# ===========================================================================
#
# mode="poll"   → WAIL polls the refresher() every interval_seconds.
#                 Agents always read a fresh value without triggering a fetch.
#
# mode="push"   → An external program owns the updates. It calls push() in-process
#                 or POST /_wail/cache/push/{key} over HTTP whenever data changes.
#                 WAIL trusts the last value it received. No polling is done.
#                 Use when the source system already emits change events
#                 (webhooks, POS callbacks, booking confirmations, etc.).
#
# mode="static" → Manual only. Changes via cache.set() or .refresh().
#                 Good for config-like data or owner-managed content.
#

# poll: WAIL polls the booking API every 20s and keeps the schedule fresh.
def fetch_schedule():
    # In production: call the booking API here.
    slots = [
        {"time": "09:00", "barber_id": "tony",   "available": random.choice([True, False])},
        {"time": "09:30", "barber_id": "darius", "available": random.choice([True, False])},
        {"time": "10:00", "barber_id": "mia",    "available": random.choice([True, False])},
        {"time": "11:00", "barber_id": "tony",   "available": random.choice([True, False])},
        {"time": "14:00", "barber_id": "darius", "available": True},
        {"time": "15:30", "barber_id": "mia",    "available": True},
    ]
    return {"date": "2026-03-08", "slots": slots}

cache.register(
    "schedule",
    mode="poll",
    refresher=fetch_schedule,
    interval_seconds=20,
    initial_value=fetch_schedule(),
)

# poll: WAIL polls a queue system every 30s.
def fetch_wait_time():
    # In production: call the shop's queue system here.
    return {
        "current_queue_length": random.randint(0, 6),
        "estimated_wait_minutes": random.randint(0, 45),
    }

cache.register(
    "wait_time",
    mode="poll",
    refresher=fetch_wait_time,
    interval_seconds=30,
    initial_value=fetch_wait_time(),
)

# push: Tony's POS system calls POST /_wail/cache/push/todays_stats after every
# completed service. WAIL never polls — it relies entirely on the POS to push.
# The value here is always "what the POS last told us".
cache.register(
    "todays_stats",
    mode="push",
    initial_value={
        "cuts_completed": 0,
        "revenue_usd": 0.0,
        "busiest_barber": None,
    },
)
# To push from the POS (or any external program):
#   POST http://localhost:8000/_wail/cache/push/todays_stats
#   Body: {"cuts_completed": 7, "revenue_usd": 245.0, "busiest_barber": "tony"}
#
# Or in-process:
#   cache.push("todays_stats", {"cuts_completed": 7, ...})

# static: shop owner updates promotions manually (e.g. from the dashboard).
cache.register(
    "promotions",
    mode="static",
    initial_value={
        "active": [
            {"code": "MARCH10", "description": "10% off any service in March", "expires": "2026-03-31"},
            {"code": "NEWCLIENT", "description": "First haircut $25 for new clients", "expires": "2026-12-31"},
        ]
    },
)

SITE_ID = "tonys-cuts"

# ---------------------------------------------------------------------------
# Site listing
# ---------------------------------------------------------------------------

registry.register(
    path="/sites",
    description="List all sites registered with WAIL and their capabilities.",
    tags=["registry"],
    handler=lambda: {
        "sites": [
            {
                "id": SITE_ID,
                "name": "Tony's Cuts",
                "type": "barbershop",
                "location": "42 Main St, Brooklyn, NY",
                "capabilities": ["info", "services", "barbers", "availability", "schedule", "wait-time", "todays-stats", "promotions", "booking"],
            }
        ]
    },
)

# ---------------------------------------------------------------------------
# Site menu — dynamically built from the registry at call time
# ---------------------------------------------------------------------------

def tonys_menu():
    prefix = f"/sites/{SITE_ID}/"
    sub_endpoints = [
        {
            "path": ep.path,
            "method": ep.method,
            "description": ep.description,
            "tags": ep.tags,
        }
        for ep in registry.list_all()
        if ep.path.startswith(prefix) and ep.path != f"/sites/{SITE_ID}/menu"
    ]
    return {
        "_instructions": (
            "You are interacting with Tony's Cuts via WAIL. "
            "You MUST read this menu in full before calling any other endpoint. "
            "Each entry lists the path, HTTP method, and a description of what it returns. "
            "Only call endpoints listed here. "
            "Endpoints tagged 'interact' may modify state — confirm with the user before calling them."
        ),
        "site": SITE_ID,
        "endpoints": sub_endpoints,
    }

registry.register(
    path=f"/sites/{SITE_ID}/menu",
    description="Start here. Full list of available endpoints for Tony's Cuts with descriptions. Read this before calling anything else.",
    tags=["observe", "meta"],
    handler=tonys_menu,
)

# ---------------------------------------------------------------------------
# Shop info
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}",
    description="General info about Tony's Cuts — hours, location, contact.",
    tags=["observe"],
    handler=lambda: {
        "id": SITE_ID,
        "name": "Tony's Cuts",
        "tagline": "Fresh cuts, old-school feel.",
        "address": "42 Main St, Brooklyn, NY 11201",
        "phone": "+1 (718) 555-0192",
        "hours": {
            "monday":    "09:00–19:00",
            "tuesday":   "09:00–19:00",
            "wednesday": "09:00–19:00",
            "thursday":  "09:00–20:00",
            "friday":    "09:00–20:00",
            "saturday":  "08:00–18:00",
            "sunday":    "closed",
        },
        "explore": f"/sites/{SITE_ID}/menu",
    },
)

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}/services",
    description="Menu of services and prices at Tony's Cuts.",
    tags=["observe"],
    handler=lambda: {
        "services": [
            {"id": "haircut",        "name": "Haircut",              "duration_min": 30, "price_usd": 35},
            {"id": "beard-trim",     "name": "Beard Trim",           "duration_min": 20, "price_usd": 20},
            {"id": "haircut-beard",  "name": "Haircut + Beard",      "duration_min": 50, "price_usd": 50},
            {"id": "kids-cut",       "name": "Kids Cut (under 12)",  "duration_min": 20, "price_usd": 25},
            {"id": "hot-towel-shave","name": "Hot Towel Shave",      "duration_min": 40, "price_usd": 45},
        ]
    },
)

# ---------------------------------------------------------------------------
# Barbers
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}/barbers",
    description="Barbers working at Tony's Cuts.",
    tags=["observe"],
    handler=lambda: {
        "barbers": [
            {"id": "tony",   "name": "Tony Marchetti", "specialties": ["fades", "hot towel shave"], "since": 2009},
            {"id": "darius", "name": "Darius Webb",    "specialties": ["textured hair", "beard sculpting"], "since": 2018},
            {"id": "mia",    "name": "Mia Santos",     "specialties": ["kids cuts", "scissor cuts"], "since": 2021},
        ]
    },
)

# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}/availability",
    description="Open booking slots at Tony's Cuts for the next 3 days.",
    tags=["observe", "booking"],
    handler=lambda: {
        "site": SITE_ID,
        "slots": [
            {"date": "2026-03-08", "time": "09:00", "barber_id": "tony",   "available": True},
            {"date": "2026-03-08", "time": "09:30", "barber_id": "darius", "available": True},
            {"date": "2026-03-08", "time": "10:00", "barber_id": "tony",   "available": False},
            {"date": "2026-03-08", "time": "10:30", "barber_id": "mia",    "available": True},
            {"date": "2026-03-09", "time": "09:00", "barber_id": "tony",   "available": True},
            {"date": "2026-03-09", "time": "11:00", "barber_id": "darius", "available": True},
            {"date": "2026-03-09", "time": "14:00", "barber_id": "mia",    "available": False},
            {"date": "2026-03-10", "time": "10:00", "barber_id": "tony",   "available": True},
            {"date": "2026-03-10", "time": "13:30", "barber_id": "darius", "available": True},
        ],
    },
)

# ---------------------------------------------------------------------------
# Cache-bound endpoints — output is always the current cache value
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}/schedule",
    description="Live availability schedule for today. Auto-refreshes every 20s from the booking system.",
    tags=["observe", "booking", "live"],
    bind="schedule",         # → always returns cache.get("schedule")
)

registry.register(
    path=f"/sites/{SITE_ID}/wait-time",
    description="Current queue length and estimated wait time. Auto-refreshes every 30s.",
    tags=["observe", "live"],
    bind="wait_time",
)

registry.register(
    path=f"/sites/{SITE_ID}/todays-stats",
    description="Live stats for today — cuts completed, revenue, busiest barber. Pushed by the POS system after every service.",
    tags=["observe", "push"],
    bind="todays_stats",
)

registry.register(
    path=f"/sites/{SITE_ID}/promotions",
    description="Active promotions and discount codes. Updated by the shop owner when offers change.",
    tags=["observe"],
    bind="promotions",
)

# ---------------------------------------------------------------------------
# Booking (interact — would require auth in a real deployment)
# ---------------------------------------------------------------------------

registry.register(
    path=f"/sites/{SITE_ID}/booking",
    description="Create a booking at Tony's Cuts. Requires: barber_id, service_id, date, time, customer name.",
    tags=["interact", "booking"],
    method="POST",
    handler=lambda: {
        "status": "confirmed",
        "booking_id": "bk-00042",
        "message": "Booking confirmed. See you at Tony's Cuts!",
    },
)

# ---------------------------------------------------------------------------
# Startup summary
# ---------------------------------------------------------------------------

print("\nWAIL demo — Tony's Cuts")
print("=" * 40)
for ep in registry.list_all():
    print(f"  {ep.method:<6} {ep.path}")
print("=" * 40)
print("Docs: http://localhost:8000\n")

if __name__ == "__main__":
    wail.serve(port=8000)
