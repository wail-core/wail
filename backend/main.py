"""
WAIL backend entry point.

This file creates the server, configures middleware, and mounts routers.
All business logic and route definitions live in routers/.

Adding a new integration
------------------------
1. Create routers/<your_integration>.py with an APIRouter.
2. Import and include the router below — main.py grows by exactly two lines.
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

from wail import WAIL

from routers.auth import router as auth_router
from routers.calendar import router as calendar_router
from routers.booking import router as booking_router
from routers.trust import router as trust_router
from routers.payment import router as payment_router
from routers.config import router as config_router
from routers.plugins import router as plugins_router


# ── Application ───────────────────────────────────────────────────────────────

wail = WAIL()
app  = wail.app

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(config_router)
app.include_router(plugins_router)
app.include_router(calendar_router)
app.include_router(booking_router)
app.include_router(trust_router)
app.include_router(payment_router)


# ── Entry point ───────────────────────────────────────────────────────────────

def serve():
    """Called by the wail-serve CLI script defined in pyproject.toml."""
    wail.serve(port=8000)
