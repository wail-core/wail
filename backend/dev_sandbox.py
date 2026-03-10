"""
wail dev — local plugin sandbox.

Usage:
    wail-dev my_plugin.py
    wail-dev my_plugin.py --port 7000

Loads the given Python file, auto-detects every AbstractIntegration subclass
defined in it, and spins up a self-contained development server with:

  GET /                 — browser dashboard (no npm, no CDN)
  GET /manifest         — live integration manifest (schema + extras)
  POST /call-tool       — invoke an MCP tool in-process and show the result
  POST /simulate-webhook — fire a fake payment webhook and show the dispatch log
  GET /events           — SSE stream that pushes "reload" when the plugin file changes

The dashboard opens automatically in the default browser.

Plugin file contract
--------------------
The file may define any number of AbstractIntegration subclasses.  They can
optionally accept zero arguments (the sandbox instantiates them without args)
or have a module-level instance whose name ends with "integration".

Example plugin (my_plugin.py)::

    from integrations.base import AbstractIntegration

    class CrmIntegration(AbstractIntegration):
        name         = "crm"
        display_name = "CRM"
        description  = "Push contacts into HubSpot after each booking."

        def get_settings_schema(self):
            return {
                "type": "object",
                "properties": {
                    "api_key":   {"type": "string",  "description": "HubSpot API key"},
                    "owner_id":  {"type": "string",  "description": "Default owner ID"},
                    "auto_push": {"type": "boolean", "description": "Push on every booking"},
                },
            }

        def get_mcp_tools(self, wail_base, api_key):
            def search_contacts(query: str) -> dict:
                "Search HubSpot contacts by name or email."
                return {"contacts": [], "note": "Sandbox — no real API call made"}
            return [search_contacts]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import queue
import sys
import threading
import time
import types
import webbrowser
from pathlib import Path
from typing import Any

# ── HTML dashboard (self-contained, no CDN) ───────────────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>WAIL Dev Sandbox</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: #0e0e11; color: #e4e4e7; min-height: 100vh; padding: 32px 24px;
  }
  h1 { font-size: 1.35rem; font-weight: 700; color: #fff; letter-spacing: -.02em; }
  h2 { font-size: .85rem; font-weight: 600; color: #a1a1aa; text-transform: uppercase;
       letter-spacing: .08em; margin-bottom: 12px; }
  h3 { font-size: .95rem; font-weight: 600; color: #e4e4e7; margin-bottom: 8px; }
  .subtitle { font-size: .8rem; color: #71717a; margin-top: 4px; }
  .header { display: flex; align-items: center; justify-content: space-between;
            margin-bottom: 32px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
         display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .reload-badge { font-size: .7rem; background: #1d4ed8; color: #bfdbfe;
                  padding: 3px 8px; border-radius: 4px; display: none; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
  .card { background: #18181b; border: 1px solid #27272a; border-radius: 12px;
          padding: 20px; }
  .card + .card { margin-top: 0; }
  label { display: block; font-size: .78rem; color: #a1a1aa; margin-bottom: 4px; }
  input[type=text], input[type=password], textarea, select {
    width: 100%; background: #09090b; border: 1px solid #3f3f46;
    border-radius: 6px; padding: 7px 10px; color: #e4e4e7;
    font-size: .82rem; font-family: inherit; outline: none;
    transition: border-color .15s;
  }
  input:focus, textarea:focus, select:focus { border-color: #6366f1; }
  textarea { min-height: 90px; resize: vertical; font-family: "SF Mono", monospace; }
  .field-row { margin-bottom: 12px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 14px; border-radius: 6px; font-size: .8rem;
    font-weight: 500; cursor: pointer; border: none;
    transition: opacity .15s, background .15s;
  }
  .btn:hover { opacity: .85; }
  .btn-primary { background: #4f46e5; color: #fff; }
  .btn-ghost   { background: #27272a; color: #d4d4d8; }
  .btn-danger  { background: #7f1d1d; color: #fca5a5; }
  .tool-select { margin-bottom: 10px; }
  .result {
    background: #09090b; border: 1px solid #27272a; border-radius: 6px;
    padding: 10px 12px; font-size: .75rem; font-family: "SF Mono", monospace;
    color: #a3e635; white-space: pre-wrap; word-break: break-all;
    max-height: 240px; overflow-y: auto; margin-top: 10px; display: none;
  }
  .error-result { color: #f87171; }
  .tag { display: inline-block; font-size: .68rem; padding: 2px 7px;
         border-radius: 4px; background: #27272a; color: #a1a1aa;
         border: 1px solid #3f3f46; margin-right: 4px; margin-bottom: 4px; }
  .section-sep { border: none; border-top: 1px solid #27272a; margin: 24px 0; }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #166534;
           color: #bbf7d0; font-size: .8rem; padding: 8px 14px; border-radius: 8px;
           display: none; z-index: 99; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1><span class="dot"></span> WAIL Dev Sandbox</h1>
    <p class="subtitle" id="file-label">Loading…</p>
  </div>
  <span class="reload-badge" id="reload-badge">&#8635; Reloaded</span>
</div>

<div id="content">
  <p class="subtitle">Fetching manifest…</p>
</div>

<div class="toast" id="toast"></div>

<script>
const API = window.location.origin

// ── SSE hot-reload ──────────────────────────────────────────────────────────
const es = new EventSource(`${API}/events`)
es.addEventListener("reload", () => {
  const badge = document.getElementById("reload-badge")
  badge.style.display = "inline-block"
  setTimeout(() => badge.style.display = "none", 2000)
  loadManifest()
})

// ── Toast helper ────────────────────────────────────────────────────────────
function showToast(msg, color = "#166534", text = "#bbf7d0") {
  const t = document.getElementById("toast")
  t.textContent = msg; t.style.background = color; t.style.color = text
  t.style.display = "block"
  clearTimeout(t._timer)
  t._timer = setTimeout(() => t.style.display = "none", 2500)
}

// ── Manifest + render ────────────────────────────────────────────────────────
async function loadManifest() {
  const res  = await fetch(`${API}/manifest`)
  const info = await fetch(`${API}/info`).then(r => r.json())
  document.getElementById("file-label").textContent = `Plugin: ${info.file}`
  const integrations = await res.json()
  renderManifest(integrations)
}

function renderManifest(integrations) {
  const el = document.getElementById("content")
  if (!integrations.length) {
    el.innerHTML = `<p class="subtitle">No AbstractIntegration subclasses found in the plugin file.</p>`
    return
  }
  el.innerHTML = integrations.map((i, idx) => renderIntegration(i, idx)).join("")
}

function renderIntegration(i, idx) {
  const props = (i.settings_schema && i.settings_schema.properties) || {}
  const hasSchema = Object.keys(props).length > 0
  const schemaSection = hasSchema ? `
    <div style="margin-top:16px">
      <h2>Settings schema</h2>
      ${Object.entries(props).map(([k, p]) => `
        <div class="field-row">
          <label>${p.description || k} <span class="tag">${p.type}</span></label>
          ${p.enum
            ? `<select id="s-${idx}-${k}"><option value="">—</option>${p.enum.map(v=>`<option>${v}</option>`).join("")}</select>`
            : p.type === "boolean"
              ? `<select id="s-${idx}-${k}"><option value="true">true</option><option value="false">false</option></select>`
              : `<input type="${k.toLowerCase().includes('secret')||k.toLowerCase().includes('key')||k.toLowerCase().includes('pass') ? 'password' : 'text'}" id="s-${idx}-${k}" placeholder="${k}">`
          }
        </div>
      `).join("")}
      <button class="btn btn-primary" onclick="saveSettings(${idx}, '${i.name}', ${JSON.stringify(Object.keys(props))})">
        Save settings
      </button>
      <div class="result" id="settings-result-${idx}"></div>
    </div>` : ""

  return `
  <div class="grid" style="margin-bottom:20px">
    <div class="card">
      <h3>${i.display_name}</h3>
      <p class="subtitle" style="margin-bottom:10px">${i.description}</p>
      <div>
        <span class="tag">name: ${i.name}</span>
        <span class="tag">enabled: ${i.enabled}</span>
        ${"connected" in i ? `<span class="tag">connected: ${i.connected}</span>` : ""}
      </div>
      ${schemaSection}
    </div>
    <div style="display:flex;flex-direction:column;gap:20px">
      ${renderToolCard(i, idx)}
      ${renderWebhookCard(i, idx)}
    </div>
  </div>`
}

function renderToolCard(i, idx) {
  const tools = i.mcp_tools || []
  if (!tools.length) return `<div class="card"><h2>MCP Tools</h2><p class="subtitle">No MCP tools registered.</p></div>`
  return `
  <div class="card">
    <h2>Test MCP tool</h2>
    <div class="field-row tool-select">
      <label>Tool</label>
      <select id="tool-select-${idx}" onchange="renderToolArgs(${idx})">
        ${tools.map(t => `<option value="${t.name}" data-sig='${JSON.stringify(t.params)}'>${t.name}</option>`).join("")}
      </select>
    </div>
    <div id="tool-args-${idx}"></div>
    <button class="btn btn-primary" onclick="callTool(${idx}, '${i.name}')">&#9654; Run tool</button>
    <div class="result" id="tool-result-${idx}"></div>
  </div>`
}

function renderToolArgs(idx) {
  const sel    = document.getElementById(`tool-select-${idx}`)
  const params = JSON.parse(sel.selectedOptions[0].dataset.sig || "[]")
  const el     = document.getElementById(`tool-args-${idx}`)
  el.innerHTML = params.map(p => `
    <div class="field-row">
      <label>${p.name} <span class="tag">${p.annotation}</span></label>
      <input type="text" id="arg-${idx}-${p.name}" placeholder="${p.name}">
    </div>`).join("")
}

function renderWebhookCard(i, idx) {
  return `
  <div class="card">
    <h2>Simulate webhook</h2>
    <p class="subtitle" style="margin-bottom:10px">Fires a fake payment-complete event through the integration's webhook handler.</p>
    <div class="field-row">
      <label>Action</label>
      <input type="text" id="wh-action-${idx}" value="create_booking" placeholder="e.g. create_booking">
    </div>
    <div class="field-row">
      <label>Payload (JSON)</label>
      <textarea id="wh-payload-${idx}" placeholder='{"date":"2026-03-10","time":"10:00",...}'></textarea>
    </div>
    <div class="field-row">
      <label>Amount (cents)</label>
      <input type="text" id="wh-amount-${idx}" value="2500">
    </div>
    <button class="btn btn-ghost" onclick="simulateWebhook(${idx}, '${i.name}')">&#9654; Fire webhook</button>
    <div class="result" id="wh-result-${idx}"></div>
  </div>`
}

// ── After render, populate tool args for first tool ─────────────────────────
function renderManifest(integrations) {
  const el = document.getElementById("content")
  if (!integrations.length) {
    el.innerHTML = `<p class="subtitle">No AbstractIntegration subclasses found.</p>`
    return
  }
  el.innerHTML = integrations.map((i, idx) => renderIntegration(i, idx)).join("")
  integrations.forEach((_, idx) => {
    const sel = document.getElementById(`tool-select-${idx}`)
    if (sel) renderToolArgs(idx)
  })
}

// ── API calls ────────────────────────────────────────────────────────────────
async function saveSettings(idx, name, keys) {
  const body = {}
  keys.forEach(k => {
    const el = document.getElementById(`s-${idx}-${k}`)
    if (el) body[k] = el.value
  })
  const res  = await fetch(`${API}/settings/${name}`, {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body)
  })
  const data = await res.json()
  showResult(`settings-result-${idx}`, data)
  showToast("Settings saved")
}

async function callTool(idx, integrationName) {
  const sel    = document.getElementById(`tool-select-${idx}`)
  const tool   = sel ? sel.value : ""
  const params = JSON.parse(sel.selectedOptions[0].dataset.sig || "[]")
  const args   = {}
  params.forEach(p => {
    const el = document.getElementById(`arg-${idx}-${p.name}`)
    if (el) {
      const v = el.value
      args[p.name] = (p.annotation === "int" || p.annotation === "float") ? Number(v) : v
    }
  })
  const res  = await fetch(`${API}/call-tool`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ integration: integrationName, tool, args })
  })
  const data = await res.json()
  showResult(`tool-result-${idx}`, data, !res.ok)
}

async function simulateWebhook(idx, integrationName) {
  let payload = {}
  try { payload = JSON.parse(document.getElementById(`wh-payload-${idx}`).value || "{}") }
  catch { showToast("Invalid JSON in payload", "#7f1d1d", "#fca5a5"); return }
  const res  = await fetch(`${API}/simulate-webhook`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      integration: integrationName,
      action:      document.getElementById(`wh-action-${idx}`).value,
      payload,
      amount_cents: parseInt(document.getElementById(`wh-amount-${idx}`).value) || 0,
    })
  })
  const data = await res.json()
  showResult(`wh-result-${idx}`, data, !res.ok)
}

function showResult(id, data, isError = false) {
  const el = document.getElementById(id)
  el.textContent = JSON.stringify(data, null, 2)
  el.className   = "result" + (isError ? " error-result" : "")
  el.style.display = "block"
}

loadManifest()
</script>
</body>
</html>"""


# ── Plugin loader ─────────────────────────────────────────────────────────────

def _load_integrations(plugin_path: Path) -> list:
    """
    Import the plugin file and return all AbstractIntegration subclasses found in it.

    Tries three strategies in order:
    1. Module-level instances whose name ends with "integration".
    2. Zero-arg subclasses of AbstractIntegration — instantiated automatically.
    3. Subclasses that need args — skipped with a warning.
    """
    from integrations.base import AbstractIntegration

    spec   = importlib.util.spec_from_file_location("_wail_plugin", plugin_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    found: list = []
    seen:  set  = set()

    # Strategy 1: pre-built instances
    for attr_name, obj in inspect.getmembers(module):
        if (
            isinstance(obj, AbstractIntegration)
            and type(obj) not in seen
        ):
            found.append(obj)
            seen.add(type(obj))

    # Strategy 2: instantiable subclasses not already in found
    for attr_name, cls in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(cls, AbstractIntegration)
            and cls is not AbstractIntegration
            and cls not in seen
        ):
            try:
                found.append(cls())
                seen.add(cls)
            except TypeError:
                print(
                    f"[wail dev] Warning: {cls.__name__} requires constructor arguments — "
                    "create an instance in your plugin file to include it."
                )

    return found


def _integration_manifest(integration) -> dict:
    """Build the manifest entry for a single integration."""
    entry: dict = {
        "name":            integration.name,
        "display_name":    integration.display_name,
        "description":     integration.description,
        "enabled":         True,  # always enabled in sandbox
        "settings_schema": integration.get_settings_schema(),
        "mcp_tools":       _describe_tools(integration),
    }
    entry.update(integration.get_manifest_extras())
    return entry


def _describe_tools(integration) -> list[dict]:
    """Return a list of {name, doc, params} for every MCP tool in the integration."""
    try:
        tools = integration.get_mcp_tools("http://localhost:8000", "")
    except Exception:
        return []
    result = []
    for fn in tools:
        sig    = inspect.signature(fn)
        params = [
            {
                "name":       name,
                "annotation": (
                    param.annotation.__name__
                    if param.annotation is not inspect.Parameter.empty
                    and hasattr(param.annotation, "__name__")
                    else "string"
                ),
                "default": (
                    None
                    if param.default is inspect.Parameter.empty
                    else param.default
                ),
            }
            for name, param in sig.parameters.items()
        ]
        result.append({
            "name":   fn.__name__,
            "doc":    (fn.__doc__ or "").strip(),
            "params": params,
        })
    return result


# ── App state (mutable, reloaded on file change) ──────────────────────────────

class _SandboxState:
    def __init__(self):
        self.integrations: list         = []
        self.by_name:      dict         = {}
        self.tool_fns:     dict         = {}   # (integration_name, tool_name) -> callable
        self._lock = threading.Lock()

    def reload(self, plugin_path: Path) -> None:
        try:
            integrations = _load_integrations(plugin_path)
        except Exception as e:
            print(f"[wail dev] Reload error: {e}")
            return
        by_name:  dict = {}
        tool_fns: dict = {}
        for integration in integrations:
            by_name[integration.name] = integration
            try:
                for fn in integration.get_mcp_tools("http://localhost:8000", ""):
                    tool_fns[(integration.name, fn.__name__)] = fn
            except Exception:
                pass
        with self._lock:
            self.integrations = integrations
            self.by_name      = by_name
            self.tool_fns     = tool_fns

    def manifest(self) -> list[dict]:
        with self._lock:
            return [_integration_manifest(i) for i in self.integrations]


# ── FastAPI sandbox server ────────────────────────────────────────────────────

def _build_app(plugin_path: Path, state: _SandboxState, reload_queue: queue.Queue) -> Any:
    """Build and return the FastAPI sandbox app."""
    import asyncio
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    app = FastAPI(title="WAIL Dev Sandbox", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return _DASHBOARD_HTML

    @app.get("/info")
    async def info():
        return {"file": str(plugin_path)}

    @app.get("/manifest")
    async def manifest():
        return state.manifest()

    @app.get("/events")
    async def sse_events(request: Request):
        """SSE stream — sends 'reload' whenever the plugin file changes."""
        async def generator():
            yield "retry: 1000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    reload_queue.get_nowait()
                    yield "event: reload\ndata: {}\n\n"
                except queue.Empty:
                    pass
                await asyncio.sleep(0.3)
        return StreamingResponse(generator(), media_type="text/event-stream")

    @app.post("/settings/{name}")
    async def save_settings(name: str, request: Request):
        body = await request.json()
        integration = state.by_name.get(name)
        if not integration:
            return JSONResponse({"error": f"No integration '{name}'"}, status_code=404)
        # For sandbox, just echo back — real implementations persist via CredentialStore
        return {"status": "ok", "received": body, "note": "Sandbox: values not persisted"}

    @app.post("/call-tool")
    async def call_tool(request: Request):
        body             = await request.json()
        integration_name = body.get("integration", "")
        tool_name        = body.get("tool", "")
        args             = body.get("args", {})
        fn = state.tool_fns.get((integration_name, tool_name))
        if fn is None:
            return JSONResponse(
                {"error": f"Tool '{tool_name}' not found on '{integration_name}'"},
                status_code=404,
            )
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)
            return {"result": result}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/simulate-webhook")
    async def simulate_webhook(request: Request):
        """
        Simulate a payment-complete webhook.

        Bypasses signature verification (sandbox mode) and calls the integration's
        registered action handler with the provided payload.
        """
        body             = await request.json()
        integration_name = body.get("integration", "")
        action           = body.get("action", "")
        payload          = body.get("payload", {})
        amount_cents     = body.get("amount_cents", 0)

        integration = state.by_name.get(integration_name)
        if integration is None:
            return JSONResponse(
                {"error": f"No integration '{integration_name}'"},
                status_code=404,
            )

        # Try payment integration dispatch
        handler = getattr(integration, "_action_handlers", {}).get(action)
        if handler is None:
            return JSONResponse(
                {
                    "note": (
                        f"No action handler registered for '{action}' "
                        f"on '{integration_name}'. "
                        "Register one with integration.register_action(name, fn)."
                    ),
                    "payload": payload,
                    "simulated_event": {
                        "type":   "checkout.session.completed",
                        "action": action,
                        "amount_cents": amount_cents,
                    },
                }
            )
        try:
            result = handler(payload)
            return {"status": "dispatched", "action": action, "result": result}
        except Exception as e:
            return JSONResponse(
                {"status": "handler_error", "action": action, "error": str(e)},
                status_code=500,
            )

    return app


# ── File watcher ──────────────────────────────────────────────────────────────

def _watch_file(plugin_path: Path, state: _SandboxState, rq: queue.Queue) -> None:
    """Block forever, reloading state whenever plugin_path changes."""
    try:
        from watchfiles import watch as wf_watch
        for _ in wf_watch(plugin_path):
            print(f"[wail dev] {plugin_path.name} changed — reloading…")
            state.reload(plugin_path)
            try:
                rq.put_nowait("reload")
            except queue.Full:
                pass
    except ImportError:
        # Fallback: poll mtime
        last_mtime = plugin_path.stat().st_mtime
        while True:
            time.sleep(0.8)
            try:
                mtime = plugin_path.stat().st_mtime
            except OSError:
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                print(f"[wail dev] {plugin_path.name} changed — reloading…")
                state.reload(plugin_path)
                try:
                    rq.put_nowait("reload")
                except queue.Full:
                    pass


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wail-dev",
        description="WAIL plugin sandbox — test your integration locally.",
    )
    parser.add_argument("plugin", help="Path to the plugin Python file, e.g. my_plugin.py")
    parser.add_argument("--port", type=int, default=7000, help="Local port (default: 7000)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser")
    args = parser.parse_args()

    plugin_path = Path(args.plugin).resolve()
    if not plugin_path.exists():
        print(f"Error: {plugin_path} not found.", file=sys.stderr)
        sys.exit(1)

    # Add the plugin's directory to sys.path so local imports work
    plugin_dir = str(plugin_path.parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    print(f"[wail dev] Loading {plugin_path.name}…")
    state        = _SandboxState()
    reload_queue: queue.Queue = queue.Queue(maxsize=1)
    state.reload(plugin_path)

    n = len(state.integrations)
    if n == 0:
        print("[wail dev] Warning: no AbstractIntegration subclasses found.")
    else:
        names = ", ".join(i.display_name for i in state.integrations)
        print(f"[wail dev] Found {n} integration(s): {names}")

    app = _build_app(plugin_path, state, reload_queue)

    # File watcher runs in a daemon thread
    watcher = threading.Thread(
        target=_watch_file,
        args=(plugin_path, state, reload_queue),
        daemon=True,
    )
    watcher.start()

    url = f"http://localhost:{args.port}"
    print(f"[wail dev] Sandbox running at {url}")
    if not args.no_browser:
        # Delay slightly so the server is up before the browser opens
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
