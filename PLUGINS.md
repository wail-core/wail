# Building & Publishing WAIL Plugins

A WAIL plugin is a regular Python package on PyPI. When a user installs it,
WAIL discovers it automatically at startup — no code change required on their side.

---

## Quickstart

```bash
pip install wail-core
wail-create-plugin
```

Answer the prompts (name, OAuth?, MCP tools?). A complete package directory is
generated instantly. Fill in your stubs, run the tests, and publish.

---

## Package structure

```
wail-plugin-hubspot/
├── pyproject.toml
├── hubspot_integration/
│   ├── __init__.py          ← exports plugin_integration (the entry point target)
│   ├── connection.py        ← OAuth2 connection (only when OAuth is needed)
│   └── integration.py       ← AbstractIntegration subclass
└── tests/
    ├── __init__.py
    └── test_hubspot.py      ← uses wail_test_client fixture automatically
```

---

## The entry point contract

The single line that makes WAIL discover your plugin is in `pyproject.toml`:

```toml
[project.entry-points."wail.plugins"]
hubspot = "hubspot_integration:plugin_integration"
```

- **Group**: `wail.plugins` — fixed, never changes.
- **Key**: your plugin slug (any unique string; shown in log messages).
- **Value**: `module:attribute` pointing to either:
  - An **AbstractIntegration instance** (preferred). WAIL loads it as-is.
  - An **AbstractIntegration subclass**. WAIL calls `cls()` with no arguments.

### Why use an instance, not a class?

Instances let you inject credentials or connection objects at import time:

```python
# hubspot_integration/__init__.py
import os
from .connection import HubspotConnection
from .integration import HubspotIntegration

plugin_integration = HubspotIntegration(
    connection=HubspotConnection(api_key=os.environ.get("HUBSPOT_API_KEY", ""))
)
```

WAIL loads `plugin_integration` exactly as it is — the credentials you set
at import time are the ones used at runtime. No separate config step needed.

---

## What WAIL loads at startup

```python
# Equivalent to what WailApp.__init__ runs automatically:
from importlib.metadata import entry_points

for ep in entry_points(group="wail.plugins"):
    plugin = ep.load()          # imports your module
    wail_app.register(plugin)   # adds to ALL_INTEGRATIONS
```

Any package that declares a `wail.plugins` entry point is loaded. Order matches
pip's installation order; built-in integrations (calendar, booking, payment)
always come first.

### Failures are isolated

If your plugin raises an exception during import or instantiation, WAIL prints
a warning to stderr and continues. A broken plugin never prevents the server
from starting.

---

## Full pyproject.toml template

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "wail-plugin-hubspot"
version = "0.1.0"
description = "WAIL plugin — push contacts to HubSpot after each booking."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
keywords = ["wail", "wail-plugin", "hubspot"]
classifiers = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: MIT License",
]
dependencies = [
    "wail-core>=0.1.0",
]

[project.optional-dependencies]
test = ["pytest>=7.0.0", "wail-core[test]>=0.1.0"]

[project.entry-points."wail.plugins"]
hubspot = "hubspot_integration:plugin_integration"

[tool.setuptools.packages.find]
where = ["."]
```

---

## Testing your plugin

`wail-core[test]` provides pytest fixtures that are auto-registered — no
`conftest.py` needed.

```python
# tests/test_hubspot.py
from wail_testing import WailTestClient, validate_plugin
from hubspot_integration import plugin_integration
from hubspot_integration.integration import HubspotIntegration


def test_contract():
    """Assert every AbstractIntegration requirement."""
    validate_plugin(plugin_integration)


def test_appears_in_manifest(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[HubspotIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        r     = client.get("/integrations/manifest")
        names = [i["name"] for i in r.json()]
        assert "hubspot" in names


def test_mcp_tool(mock_calendar, mock_payment):
    with WailTestClient(
        plugins=[HubspotIntegration()],
        calendar_conn=mock_calendar,
        payment_conn=mock_payment,
    ) as client:
        result = client.call_mcp_tool("hubspot", "search_contacts", query="Alice")
        assert isinstance(result, dict)
```

Run tests:

```bash
pip install -e .[test]
pytest
```

---

## Publishing to PyPI

```bash
pip install build twine
python -m build
twine upload dist/*
```

Users install with:

```bash
pip install wail-plugin-hubspot
```

WAIL discovers it automatically on the next server start. No config, no code edits.

---

## Naming convention

| What                    | Convention                                   |
|-------------------------|----------------------------------------------|
| PyPI package name       | `wail-plugin-{slug}` (hyphen-separated)      |
| Python package (import) | `{slug}_integration` (underscore-separated)  |
| Entry point key         | `{slug}` (matches `AbstractIntegration.name`)|
| Integration slug        | lowercase, alphanumeric + underscores        |

Examples: `wail-plugin-hubspot`, `wail-plugin-acuity`, `wail-plugin-zendesk-crm`

---

## AbstractIntegration checklist

Before publishing, run `validate_plugin(plugin_integration)` in your tests.
It verifies:

- [ ] `name`, `display_name`, `description` class variables are set
- [ ] `name` is a valid slug (lowercase, alphanumeric + underscores)
- [ ] `get_settings_schema()` returns `{"type": "object", "properties": {...}}`
- [ ] `get_mcp_tools()` returns a list of callables
- [ ] Every MCP tool has a non-empty docstring
- [ ] Every MCP tool parameter has a JSON-serialisable type annotation
- [ ] `get_manifest_extras()` returns a dict
- [ ] `is_enabled()` / `set_enabled()` work without errors
