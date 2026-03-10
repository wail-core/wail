"""
wail create-plugin — interactive scaffold for WAIL plugin packages.

Asks a few questions, then generates a fully structured Python package that
is immediately installable, testable, and publishable to PyPI.

Generated structure
-------------------
    wail-plugin-{slug}/
    ├── pyproject.toml
    ├── {slug}_integration/
    │   ├── __init__.py
    │   ├── connection.py       (only when OAuth is needed)
    │   └── integration.py
    └── tests/
        ├── __init__.py
        └── test_{slug}.py

Usage
-----
    wail-create-plugin                     # interactive mode
    wail-create-plugin --slug hubspot \\
        --display "HubSpot CRM" \\
        --description "Push contacts to HubSpot." \\
        --oauth --tools                    # non-interactive / CI mode

Publishing to PyPI
------------------
After filling in the stubs:

    cd wail-plugin-{slug}
    pip install build twine
    python -m build
    twine upload dist/*

Users install with:

    pip install wail-plugin-{slug}

WAIL discovers it automatically via the ``wail.plugins`` entry point —
no code change needed on the user's side.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from textwrap import dedent


# ── Template helpers ──────────────────────────────────────────────────────────

def _class_prefix(slug: str) -> str:
    """Turn "hubspot_crm" → "HubspotCrm" (PascalCase)."""
    return "".join(word.capitalize() for word in re.split(r"[_\-]+", slug))


def _validate_slug(slug: str) -> str:
    slug = slug.strip().lower().replace("-", "_").replace(" ", "_")
    if not re.match(r"^[a-z][a-z0-9_]*$", slug):
        raise ValueError(
            f"Plugin slug must start with a letter and contain only "
            f"lowercase letters, digits, and underscores. Got: {slug!r}"
        )
    return slug


# ── File templates ────────────────────────────────────────────────────────────

def _pyproject(slug: str, display: str, description: str, has_oauth: bool) -> str:
    pkg = slug.replace("_", "-")
    extra_deps = ""
    if has_oauth:
        extra_deps = '\n    # Add your OAuth library here, e.g.:\n    # "requests-oauthlib>=1.3.0",'
    return dedent(f"""\
        [build-system]
        requires = ["setuptools>=61.0"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "wail-plugin-{pkg}"
        version = "0.1.0"
        description = "{description}"
        readme = "README.md"
        requires-python = ">=3.10"
        license = {{ text = "MIT" }}
        keywords = ["wail", "wail-plugin", "{pkg}"]
        classifiers = [
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: MIT License",
        ]
        dependencies = [
            "wail-core>=0.1.0",{extra_deps}
        ]

        [project.optional-dependencies]
        test = ["pytest>=7.0.0", "wail-core[test]>=0.1.0"]

        # ── Plugin entry point ────────────────────────────────────────────────
        #
        # This single line is what makes WAIL auto-discover this plugin when it
        # is installed. No user code change is needed — just:
        #
        #     pip install wail-plugin-{pkg}
        #
        # WAIL calls importlib.metadata.entry_points(group="wail.plugins") on
        # startup and loads every registered entry point.
        #
        # The value points to the pre-built instance in __init__.py.
        # Using an instance (not the class) lets you inject credentials or
        # connection objects at import time via environment variables.

        [project.entry-points."wail.plugins"]
        {slug} = "{slug}_integration:plugin_integration"

        [tool.setuptools.packages.find]
        where = ["."]
    """)


def _init_py(slug: str, class_prefix: str, has_oauth: bool) -> str:
    conn_import = (
        f"from .connection import {class_prefix}Connection\n"
        if has_oauth else ""
    )
    conn_arg = f"connection={class_prefix}Connection()" if has_oauth else ""
    return dedent(f"""\
        \"\"\"
        {slug}_integration — WAIL plugin package.

        Exports ``plugin_integration``, the pre-built instance registered as the
        ``wail.plugins`` entry point.  WAIL loads this instance automatically
        when the package is installed.
        \"\"\"

        from .integration import {class_prefix}Integration
        {conn_import}
        # Pre-built instance used by the entry point.
        # Inject credentials from environment variables here if needed:
        #
        #     import os
        #     plugin_integration = {class_prefix}Integration(
        #         connection={class_prefix}Connection(api_key=os.environ["MY_API_KEY"])
        #     )
        plugin_integration = {class_prefix}Integration({conn_arg})

        __all__ = ["plugin_integration", "{class_prefix}Integration"]
    """)


def _connection_py(slug: str, class_prefix: str, provider: str) -> str:
    upper = slug.upper()
    return dedent(f"""\
        \"\"\"
        {class_prefix}Connection — OAuth2 connection for {provider}.

        Stores credentials via the injected CredentialStore so they survive
        restarts. For multi-tenant deployments, WailApp.for_tenant() injects a
        per-tenant store automatically.
        \"\"\"

        from __future__ import annotations

        from connections.oauth import AbstractOAuthConnection


        class {class_prefix}Connection(AbstractOAuthConnection):
            \"\"\"
            OAuth2 connection for {provider}.

            Credential keys in the store:
                {upper}_CLIENT_ID      — OAuth application client ID
                {upper}_CLIENT_SECRET  — OAuth application client secret

            Set via the admin API::

                POST /integrations/{slug}/credentials
                {{ "client_id": "...", "client_secret": "..." }}

            Or via environment variables (fallback)::

                {upper}_CLIENT_ID=...
                {upper}_CLIENT_SECRET=...
            \"\"\"

            name         = "{slug}"
            display_name = "{provider}"
            scopes: list[str] = [
                # TODO: add the OAuth scopes your integration requires, e.g.
                # "https://api.{slug}.com/auth/read",
            ]

            # ── AbstractOAuthConnection contract ──────────────────────────────

            def _build_auth_url(self) -> str:
                \"\"\"
                Build and return the {provider} OAuth authorization URL.

                Use self.client_id, self.client_secret, and self.scopes.
                Raise RuntimeError if self.client_id is empty.
                \"\"\"
                if not self.client_id:
                    raise RuntimeError(
                        "{provider} client credentials are not set. "
                        "Call set_credentials(client_id, client_secret) first."
                    )
                # TODO: implement OAuth URL generation
                # Example (generic OAuth2):
                #   from urllib.parse import urlencode
                #   params = urlencode({{
                #       "client_id":     self.client_id,
                #       "redirect_uri":  "http://localhost:8000/auth/{slug}/callback",
                #       "scope":         " ".join(self.scopes),
                #       "response_type": "code",
                #   }})
                #   return f"https://auth.{slug}.com/oauth/authorize?{{params}}"
                raise NotImplementedError

            def _exchange_code(self, code: str) -> None:
                \"\"\"
                Exchange the authorization code for an access/refresh token.

                Call self._save_token(token_dict) to persist the token so that
                connected returns True on the next request.
                \"\"\"
                # TODO: POST the code to the provider's token endpoint, then:
                #   token = {{ "access_token": "...", "refresh_token": "...", "expires_in": 3600 }}
                #   self._save_token(token)
                raise NotImplementedError

            def _refresh_token(self) -> bool:
                \"\"\"
                Refresh the stored access token using the refresh token.

                Returns True if the token is valid (no refresh needed or
                successfully refreshed). Returns False if it can't be refreshed.

                Call self._save_token(new_token_dict) after a successful refresh.
                \"\"\"
                # TODO: check expiry, POST to token endpoint if expired:
                #   access_token = self._get_token_field("access_token")
                #   if access_token:
                #       return True   # still valid
                #   # ... refresh ...
                raise NotImplementedError

            # ── Public helpers ────────────────────────────────────────────────

            def get_access_token(self) -> str:
                \"\"\"Return the current access token string.\"\"\"
                return self._get_token_field("access_token")
    """)


def _integration_py(
    slug: str,
    class_prefix: str,
    display: str,
    description: str,
    has_oauth: bool,
    has_tools: bool,
) -> str:
    conn_import = (
        f"from .connection import {class_prefix}Connection\n"
        if has_oauth else ""
    )
    conn_param = (
        f"connection: \"{class_prefix}Connection | None\" = None"
        if has_oauth else ""
    )
    conn_assign = (
        f"        self.connection = connection or {class_prefix}Connection()"
        if has_oauth else ""
    )
    schema_example = dedent("""\
            return {
                "type": "object",
                "properties": {
                    # TODO: add configuration fields that appear in the dashboard, e.g.:
                    # "api_key": {
                    #     "type":        "string",
                    #     "description": "Your API key from the developer portal",
                    # },
                    # "auto_sync": {
                    #     "type":        "boolean",
                    #     "description": "Automatically sync after each booking",
                    # },
                },
            }""")
    tools_section = ""
    if has_tools:
        tools_section = dedent(f"""\

            def get_mcp_tools(self, wail_base: str, api_key: str) -> list:
                \"\"\"
                Return MCP tool callables for AI agents.

                Rules:
                - Every function must have a descriptive docstring — FastMCP
                  exposes it to the agent as the tool description.
                - Every parameter must have a type annotation.
                - Return values must be JSON-serialisable (dict, list, str, …).
                - Use _make_http_helpers() to call back to the WAIL REST API
                  (needed when this MCP server runs in a separate process).
                \"\"\"
                from integrations.base import _make_http_helpers
                _get, _post, _delete = _make_http_helpers(wail_base, api_key)

                # TODO: implement your tools and return them in the list below.

                def example_tool(query: str) -> dict:
                    \"\"\"
                    Example tool — replace with your real implementation.

                    Args:
                        query: Search term or identifier.

                    Returns:
                        A dict with the results.
                    \"\"\"
                    raise NotImplementedError("Replace this stub with real logic.")

                return [example_tool]
        """)
    manifest_section = ""
    if has_oauth:
        manifest_section = dedent("""\

            def get_manifest_extras(self) -> dict:
                \"\"\"Expose connection status to the dashboard.\"\"\"
                return {
                    "connected":   self.connection.connected,
                    "connect_url": f"/auth/{slug}",
                }
        """)
    indent_conn_param = f"\n        {conn_param}," if conn_param else ""
    return dedent(f"""\
        \"\"\"
        {class_prefix}Integration — WAIL integration for {display}.

        {description}
        \"\"\"

        from __future__ import annotations

        from integrations.base import AbstractIntegration
        {conn_import}

        class {class_prefix}Integration(AbstractIntegration):
            \"\"\"
            {display} integration.

            {description}
            \"\"\"

            name         = "{slug}"
            display_name = "{display}"
            description  = "{description}"

            def __init__(self,{indent_conn_param}
            ) -> None:
        {conn_assign if conn_assign else "        pass"}

            def get_settings_schema(self) -> dict:
                \"\"\"
                JSON Schema for this integration's settings.

                The dashboard renders a form from this schema automatically.
                Every property you add here appears as a labelled input field.
                \"\"\"
        {schema_example}
        {manifest_section}{tools_section}""")


def _test_py(slug: str, class_prefix: str, has_tools: bool) -> str:
    tool_test = ""
    if has_tools:
        tool_test = dedent(f"""\

            def test_mcp_tool_example(wail_test_client):
                \"\"\"
                Replace 'example_tool' with the name of your first real tool
                and adjust the kwargs to match its signature.
                \"\"\"
                with pytest.raises(NotImplementedError):
                    wail_test_client.call_mcp_tool("{slug}", "example_tool", query="test")
        """)
    return dedent(f"""\
        \"\"\"
        Tests for {class_prefix}Integration.

        wail_test_client, mock_calendar, and mock_payment are provided
        automatically by the wail-core pytest plugin — no conftest.py needed.
        \"\"\"

        import pytest
        from wail_testing import WailTestClient, validate_plugin

        from {slug}_integration import plugin_integration
        from {slug}_integration.integration import {class_prefix}Integration


        # ── Contract ──────────────────────────────────────────────────────────

        def test_plugin_satisfies_contract():
            \"\"\"Assert every AbstractIntegration requirement in one call.\"\"\"
            validate_plugin(plugin_integration)


        # ── Manifest ──────────────────────────────────────────────────────────

        @pytest.fixture
        def client(mock_calendar, mock_payment):
            \"\"\"WailTestClient with this plugin injected.\"\"\"
            with WailTestClient(
                plugins=[{class_prefix}Integration()],
                calendar_conn=mock_calendar,
                payment_conn=mock_payment,
            ) as c:
                yield c


        def test_plugin_appears_in_manifest(client):
            r = client.get("/integrations/manifest")
            assert r.status_code == 200
            names = [i["name"] for i in r.json()]
            assert "{slug}" in names, f"Expected '{{slug}}' in manifest, got: {{names}}"


        def test_plugin_manifest_has_schema(client):
            r    = client.get("/integrations/manifest")
            item = next(i for i in r.json() if i["name"] == "{slug}")
            assert "settings_schema" in item
            assert item["settings_schema"].get("type") == "object"

        {tool_test}

        # ── Agent simulation ──────────────────────────────────────────────────

        def test_booking_config_accessible(client):
            \"\"\"Smoke-test that the standard booking routes work alongside the plugin.\"\"\"
            r = client.simulate_agent_request("What services and prices do you offer?")
            assert r.status_code == 200
    """)


# ── File writer ───────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"  \033[32m✓\033[0m {path}")


# ── Interactive prompt helper ─────────────────────────────────────────────────

def _ask(prompt: str, default: str = "") -> str:
    if default:
        full_prompt = f"{prompt} [{default}]: "
    else:
        full_prompt = f"{prompt}: "
    try:
        answer = input(full_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return answer if answer else default


def _ask_bool(prompt: str, default: bool = True) -> bool:
    yn = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {yn}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not answer:
        return default
    return answer.startswith("y")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wail-create-plugin",
        description="Scaffold a WAIL plugin package ready for PyPI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            examples:
              wail-create-plugin
              wail-create-plugin --slug hubspot --display "HubSpot CRM" \\
                  --description "Push contacts to HubSpot." --oauth --tools
        """),
    )
    parser.add_argument("--slug",        help="Plugin slug, e.g. hubspot (package: wail-plugin-hubspot)")
    parser.add_argument("--display",     help="Human-readable name, e.g. 'HubSpot CRM'")
    parser.add_argument("--description", help="One-line description")
    parser.add_argument("--oauth",       action="store_true", default=None,
                        help="Generate an OAuth connection class")
    parser.add_argument("--no-oauth",    action="store_false", dest="oauth")
    parser.add_argument("--tools",       action="store_true", default=None,
                        help="Generate MCP tool stubs")
    parser.add_argument("--no-tools",    action="store_false", dest="tools")
    parser.add_argument("--output",      default=".", help="Output directory (default: .)")
    args = parser.parse_args()

    print()
    print("\033[1mWAIL Plugin Scaffolder\033[0m")
    print("─" * 40)
    print()

    # ── Collect answers ────────────────────────────────────────────────────

    # Slug
    raw_slug = args.slug or _ask("Plugin slug (e.g. hubspot, zendesk, acuity)")
    if not raw_slug:
        print("Error: plugin slug is required.", file=sys.stderr)
        sys.exit(1)
    try:
        slug = _validate_slug(raw_slug)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    class_prefix = _class_prefix(slug)
    pkg_name     = slug.replace("_", "-")

    # Display name
    display = args.display or _ask("Display name", default=class_prefix)

    # Description
    description = (
        args.description
        or _ask("One-line description", default=f"WAIL plugin for {display}.")
    )

    # OAuth
    if args.oauth is None:
        has_oauth = _ask_bool("Does this plugin need an OAuth connection?", default=False)
    else:
        has_oauth = args.oauth

    oauth_provider = ""
    if has_oauth:
        oauth_provider = _ask("OAuth provider name (e.g. HubSpot, Zendesk)", default=display)

    # MCP tools
    if args.tools is None:
        has_tools = _ask_bool("Does this plugin expose MCP tools?", default=True)
    else:
        has_tools = args.tools

    # Output directory
    output_root = Path(args.output).resolve() / f"wail-plugin-{pkg_name}"

    # ── Confirm ────────────────────────────────────────────────────────────

    print()
    print(f"\033[1mGenerating\033[0m  wail-plugin-{pkg_name}/")
    print(f"  entry point: {slug}_integration:plugin_integration")
    print(f"  OAuth:       {'yes (' + oauth_provider + ')' if has_oauth else 'no'}")
    print(f"  MCP tools:   {'yes' if has_tools else 'no'}")
    print()

    # ── Generate files ─────────────────────────────────────────────────────

    pkg_dir   = output_root / f"{slug}_integration"
    tests_dir = output_root / "tests"

    _write(output_root / "pyproject.toml",
           _pyproject(slug, display, description, has_oauth))

    _write(pkg_dir / "__init__.py",
           _init_py(slug, class_prefix, has_oauth))

    if has_oauth:
        _write(pkg_dir / "connection.py",
               _connection_py(slug, class_prefix, oauth_provider))

    _write(pkg_dir / "integration.py",
           _integration_py(slug, class_prefix, display, description,
                           has_oauth, has_tools))

    _write(tests_dir / "__init__.py", "")
    _write(tests_dir / f"test_{slug}.py",
           _test_py(slug, class_prefix, has_tools))

    # ── Next steps ─────────────────────────────────────────────────────────

    print()
    print("\033[32mDone!\033[0m  Next steps:\n")
    print(f"  cd wail-plugin-{pkg_name}")
    print( "  pip install -e .[test]          # install in editable mode with test deps")
    print( "  pytest                          # run the generated tests")
    print()
    if has_oauth:
        print( "  # Implement the three stubs in connection.py:")
        print( "  #   _build_auth_url()   _exchange_code()   _refresh_token()")
        print()
    if has_tools:
        print( "  # Replace the example_tool stub in integration.py with real logic.")
        print()
    print( "  # When ready to publish:")
    print( "  pip install build twine")
    print( "  python -m build")
    print( "  twine upload dist/*")
    print()
    print( "  # Users install and WAIL picks it up automatically:")
    print(f"  pip install wail-plugin-{pkg_name}")
    print()


if __name__ == "__main__":
    main()
