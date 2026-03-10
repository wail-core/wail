"""
Pytest configuration for the WAIL backend test suite.

sys.path is set up here so that backend modules (integrations, connections,
routers, etc.) are importable without installation.
"""

import sys
from pathlib import Path

# Add the backend directory to sys.path so imports like
# "from integrations.base import ..." work when running pytest from the repo root.
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
