"use client"

import { useEffect, useRef, useState } from "react"

const API = "http://localhost:8000"

// ── Types ─────────────────────────────────────────────────────────────────────

type SchemaProperty = {
  type:         "string" | "boolean" | "integer" | "number"
  description?: string
  enum?:        string[]
}

type Integration = {
  name:             string
  display_name:     string
  description:      string
  enabled:          boolean
  settings_schema?: { type: string; properties: Record<string, SchemaProperty> }
  connected?:       boolean
  connect_url?:     string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function isSecretField(key: string): boolean {
  return /key|secret|token|password|webhook/i.test(key)
}

function hasSchema(i: Integration): boolean {
  return !!i.settings_schema?.properties &&
    Object.keys(i.settings_schema.properties).length > 0
}

// ── Schema form ───────────────────────────────────────────────────────────────

function SchemaForm({
  integration,
  onSaved,
}: {
  integration: Integration
  onSaved: () => void
}) {
  const schema     = integration.settings_schema!
  const props      = schema.properties
  const keys       = Object.keys(props)
  const formRef    = useRef<HTMLFormElement>(null)
  const [saving, setSaving]   = useState(false)
  const [msg, setMsg]         = useState<{ ok: boolean; text: string } | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!formRef.current) return
    setSaving(true)
    setMsg(null)

    const raw    = new FormData(formRef.current)
    const body: Record<string, unknown> = {}
    keys.forEach((k) => {
      const p = props[k]
      if (p.type === "boolean") {
        body[k] = raw.get(k) === "true"
      } else if (p.type === "integer") {
        body[k] = parseInt(raw.get(k) as string, 10)
      } else if (p.type === "number") {
        body[k] = parseFloat(raw.get(k) as string)
      } else {
        body[k] = raw.get(k) ?? ""
      }
    })

    try {
      const r = await fetch(`${API}/integrations/${integration.name}/settings`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      })
      if (r.ok) {
        setMsg({ ok: true, text: "Settings saved." })
        onSaved()
      } else {
        const d = await r.json().catch(() => ({}))
        setMsg({ ok: false, text: d.detail ?? d.error ?? "Save failed." })
      }
    } catch {
      setMsg({ ok: false, text: "Could not reach the backend." })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form ref={formRef} onSubmit={handleSubmit} className="space-y-3 pt-2">
      {keys.map((key) => {
        const prop = props[key]
        const label = prop.description ?? key
        const id    = `field-${integration.name}-${key}`

        if (prop.enum) {
          return (
            <div key={key}>
              <label htmlFor={id} className="block text-xs text-muted mb-1">{label}</label>
              <select id={id} name={key}
                className="w-full bg-[#09090b] border border-white/[0.1] rounded-md px-3 py-1.5
                           text-xs text-primary focus:outline-none focus:border-accent/50">
                <option value="">— select —</option>
                {prop.enum.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
          )
        }

        if (prop.type === "boolean") {
          return (
            <div key={key} className="flex items-center gap-3">
              <select id={id} name={key} defaultValue="false"
                className="bg-[#09090b] border border-white/[0.1] rounded-md px-3 py-1.5
                           text-xs text-primary focus:outline-none focus:border-accent/50">
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </select>
              <label htmlFor={id} className="text-xs text-muted">{label}</label>
            </div>
          )
        }

        const inputType = isSecretField(key) ? "password"
          : (prop.type === "integer" || prop.type === "number") ? "number"
          : "text"

        return (
          <div key={key}>
            <label htmlFor={id} className="block text-xs text-muted mb-1">{label}</label>
            <input
              id={id} name={key} type={inputType}
              placeholder={isSecretField(key) ? "•••••••••••••••" : key}
              className="w-full bg-[#09090b] border border-white/[0.1] rounded-md px-3 py-1.5
                         text-xs text-primary placeholder:text-dim focus:outline-none
                         focus:border-accent/50 font-mono"
            />
          </div>
        )
      })}

      <div className="flex items-center gap-3 pt-1">
        <button type="submit" disabled={saving}
          className="btn-ghost py-1 px-3 text-xs disabled:opacity-50">
          {saving ? "Saving…" : "Save settings"}
        </button>
        {msg && (
          <span className={`text-xs ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>
            {msg.text}
          </span>
        )}
      </div>
    </form>
  )
}

// ── Integration card ──────────────────────────────────────────────────────────

function IntegrationCard({
  integration,
  onUpdate,
}: {
  integration: Integration
  onUpdate: (name: string, patch: Partial<Integration>) => void
}) {
  const [configOpen, setConfigOpen] = useState(false)
  const [busy, setBusy]             = useState(false)

  const showConnection = "connected" in integration
  const showConfig     = hasSchema(integration)
  const initial        = integration.display_name.charAt(0).toUpperCase()

  async function toggleEnabled() {
    setBusy(true)
    const next    = !integration.enabled
    const action  = next ? "enable" : "disable"
    await fetch(`${API}/integrations/${integration.name}/${action}`, { method: "POST" })
      .catch(() => {})
    onUpdate(integration.name, { enabled: next })
    setBusy(false)
  }

  async function connect() {
    if (integration.connect_url) {
      window.location.href = `${API}${integration.connect_url}`
    }
  }

  async function disconnect() {
    setBusy(true)
    await fetch(`${API}/integrations/${integration.name}`, { method: "DELETE" })
      .catch(() => {})
    onUpdate(integration.name, { connected: false })
    setBusy(false)
  }

  return (
    <div className="card overflow-hidden">
      {/* ── Header ── */}
      <div className="flex items-start gap-4 px-6 py-5">
        {/* Monogram avatar */}
        <div className="w-9 h-9 rounded-lg bg-accent/10 border border-accent/20
                        flex items-center justify-center shrink-0 text-accent
                        text-sm font-semibold select-none">
          {initial}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-semibold text-primary">{integration.display_name}</h2>

            {/* Enabled / disabled badge */}
            <span className={integration.enabled ? "badge-connected" : "badge-disconnected"}>
              <span className={`w-1.5 h-1.5 rounded-full ${integration.enabled ? "bg-emerald-400" : "bg-zinc-500"}`} />
              {integration.enabled ? "Enabled" : "Disabled"}
            </span>

            {/* Connection badge (only when the integration reports one) */}
            {showConnection && (
              <span className={integration.connected ? "badge-connected" : "badge-disconnected"}>
                <span className={`w-1.5 h-1.5 rounded-full ${integration.connected ? "bg-emerald-400" : "bg-zinc-500"}`} />
                {integration.connected ? "Connected" : "Not connected"}
              </span>
            )}
          </div>

          <p className="text-xs text-muted mt-0.5">{integration.description}</p>
        </div>

        {/* ── Action buttons ── */}
        <div className="flex items-center gap-2 shrink-0">
          {showConfig && (
            <button
              onClick={() => setConfigOpen((o) => !o)}
              className="btn-ghost py-1 px-3 text-xs"
              aria-expanded={configOpen}
            >
              {configOpen ? "Close" : "Configure"}
            </button>
          )}

          {showConnection && (
            integration.connected
              ? <button onClick={disconnect} disabled={busy} className="btn-danger py-1 px-3 text-xs disabled:opacity-50">
                  Disconnect
                </button>
              : <button onClick={connect} disabled={busy || !integration.connect_url}
                  className="btn-ghost py-1 px-3 text-xs disabled:opacity-40">
                  Connect
                </button>
          )}

          <button onClick={toggleEnabled} disabled={busy}
            className="btn-ghost py-1 px-3 text-xs disabled:opacity-50">
            {integration.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      {/* ── Inline settings form (slide open) ── */}
      {showConfig && configOpen && (
        <div className="border-t border-white/[0.07] px-6 py-5">
          <p className="text-xs font-medium text-muted uppercase tracking-wider mb-3">
            Settings
          </p>
          <SchemaForm
            integration={integration}
            onSaved={() => setConfigOpen(false)}
          />
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API}/integrations/manifest`)
      .then((r) => {
        if (!r.ok) throw new Error(`Backend returned ${r.status}`)
        return r.json() as Promise<Integration[]>
      })
      .then((data) => { setIntegrations(data); setLoading(false) })
      .catch((e)   => { setError(e.message);   setLoading(false) })
  }, [])

  function patchIntegration(name: string, patch: Partial<Integration>) {
    setIntegrations((prev) =>
      prev.map((i) => (i.name === name ? { ...i, ...patch } : i))
    )
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-primary tracking-tight">Integrations</h1>
        <p className="text-muted text-sm mt-1">
          Every installed plugin appears here automatically. Configure credentials,
          connect services, and enable or disable individual integrations.
        </p>
      </div>

      {loading && (
        <div className="space-y-3">
          {[1, 2, 3].map((n) => (
            <div key={n} className="card px-6 py-5 animate-pulse">
              <div className="flex items-center gap-4">
                <div className="w-9 h-9 rounded-lg bg-white/[0.05]" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-32 rounded bg-white/[0.07]" />
                  <div className="h-2 w-64 rounded bg-white/[0.05]" />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="card px-6 py-5 border-red-500/20">
          <p className="text-sm text-red-400">
            Could not load integrations: {error}
          </p>
          <p className="text-xs text-muted mt-1">
            Make sure the WAIL backend is running at{" "}
            <code className="font-mono text-dim">{API}</code>.
          </p>
        </div>
      )}

      {!loading && !error && integrations.length === 0 && (
        <div className="card px-6 py-8 text-center">
          <p className="text-sm text-muted">No integrations registered.</p>
          <p className="text-xs text-dim mt-1">
            Install a plugin package (<code className="font-mono">pip install wail-plugin-…</code>)
            and restart the server.
          </p>
        </div>
      )}

      {!loading && !error && (
        <div className="space-y-4">
          {integrations.map((integration) => (
            <IntegrationCard
              key={integration.name}
              integration={integration}
              onUpdate={patchIntegration}
            />
          ))}
        </div>
      )}
    </div>
  )
}
