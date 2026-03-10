"use client"

import { useEffect, useState } from "react"

const API = "http://localhost:8000"

type TrustConfig = {
  testing:   boolean
  mode:      "all" | "api_key" | "allowlist"
  api_keys:  string[]
  allowlist: string[]
}

const DEFAULT: TrustConfig = {
  testing:   true,
  mode:      "all",
  api_keys:  [],
  allowlist: [],
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button onClick={copy} className="btn-ghost py-1 px-2 text-xs gap-1.5">
      {copied ? (
        <>
          <svg className="w-3 h-3 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Copied
        </>
      ) : (
        <>
          <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
          </svg>
          Copy
        </>
      )}
    </button>
  )
}

export default function TrustPage() {
  const [config,     setConfig]     = useState<TrustConfig>(DEFAULT)
  const [loading,    setLoading]    = useState(true)
  const [saving,     setSaving]     = useState(false)
  const [saved,      setSaved]      = useState(false)
  const [error,      setError]      = useState("")
  const [newKey,     setNewKey]     = useState("")
  const [allowInput, setAllowInput] = useState("")

  useEffect(() => {
    fetch(`${API}/integrations/trust/config`)
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function set<K extends keyof TrustConfig>(key: K, value: TrustConfig[K]) {
    setConfig((c) => ({ ...c, [key]: value }))
    setSaved(false)
  }

  async function save() {
    setSaving(true)
    setError("")
    setSaved(false)
    try {
      const res  = await fetch(`${API}/integrations/trust/config`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ testing: config.testing, mode: config.mode, allowlist: config.allowlist }),
      })
      const data = await res.json()
      if (data.error) setError(data.error)
      else setSaved(true)
    } catch { setError("Could not reach WAIL API.") }
    finally { setSaving(false) }
  }

  async function generateKey() {
    setNewKey("")
    const res  = await fetch(`${API}/integrations/trust/api-keys`, { method: "POST" })
    const data = await res.json()
    if (data.key) {
      setNewKey(data.key)
      setConfig((c) => ({ ...c, api_keys: [...c.api_keys, data.key] }))
    }
  }

  async function revokeKey(key: string) {
    await fetch(`${API}/integrations/trust/api-keys/${encodeURIComponent(key)}`, { method: "DELETE" })
    setConfig((c) => ({ ...c, api_keys: c.api_keys.filter((k) => k !== key) }))
    if (newKey === key) setNewKey("")
  }

  function addContact() {
    const v = allowInput.trim()
    if (!v || config.allowlist.includes(v)) { setAllowInput(""); return }
    set("allowlist", [...config.allowlist, v])
    setAllowInput("")
  }

  function removeContact(contact: string) {
    set("allowlist", config.allowlist.filter((c) => c !== contact))
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-muted text-sm animate-pulse">Loading…</div>
      </div>
    )
  }

  const MODE_OPTIONS = [
    { value: "all",       label: "Open",      desc: "Anyone can perform write actions — no authentication required." },
    { value: "api_key",   label: "API Key",   desc: "Caller must send a valid key via Authorization header." },
    { value: "allowlist", label: "Allowlist", desc: "Customer contact must be pre-approved in the list below." },
  ] as const

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-primary tracking-tight">Trust &amp; Access Control</h1>
        <p className="text-muted text-sm mt-1">
          Controls who can perform write actions (e.g. booking) through the agent API. Dashboard operations are always unrestricted.
        </p>
      </div>

      {/* Testing mode banner */}
      {config.testing ? (
        <div className="alert-warning">
          <svg className="w-4 h-4 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <div>
            <p className="font-semibold">Testing mode is ON</p>
            <p className="text-amber-400/70 text-xs mt-0.5">All requests are trusted — no key or allowlist check is performed. Turn it off when going live.</p>
          </div>
        </div>
      ) : (
        <div className="alert-danger">
          <svg className="w-4 h-4 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" strokeLinecap="round" /><line x1="12" y1="16" x2="12.01" y2="16" strokeLinecap="round" />
          </svg>
          <div>
            <p className="font-semibold">Testing mode is OFF</p>
            <p className="text-red-400/70 text-xs mt-0.5">The selected access mode is enforced for all agent requests.</p>
          </div>
        </div>
      )}

      {/* Testing toggle */}
      <div className="card p-5">
        <label className="flex items-center gap-3 cursor-pointer">
          <div className="relative">
            <input
              type="checkbox"
              className="sr-only"
              checked={config.testing}
              onChange={(e) => set("testing", e.target.checked)}
            />
            <div
              className={`w-10 h-6 rounded-full transition-colors duration-200 ${config.testing ? "bg-accent" : "bg-zinc-700"}`}
            />
            <div
              className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${config.testing ? "translate-x-4" : "translate-x-0"}`}
            />
          </div>
          <div>
            <p className="text-sm font-medium text-primary">Testing mode</p>
            <p className="text-xs text-muted">All requests trusted — no auth required</p>
          </div>
        </label>
      </div>

      <div className="divider" />

      {/* Access mode */}
      <div className="space-y-4">
        <div>
          <h2 className="text-base font-semibold text-primary">Access mode</h2>
          <p className="text-muted text-xs mt-1">Applied when testing mode is off.</p>
        </div>
        <div className="space-y-2">
          {MODE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 p-4 rounded-xl border cursor-pointer transition-all duration-150 ${
                config.mode === opt.value
                  ? "border-accent/40 bg-accent/5"
                  : "border-white/[0.07] bg-surface hover:border-white/[0.12]"
              }`}
            >
              <input
                type="radio"
                name="mode"
                value={opt.value}
                checked={config.mode === opt.value}
                onChange={() => set("mode", opt.value)}
                className="mt-0.5 accent-indigo-500"
              />
              <div>
                <p className="text-sm font-medium text-primary">{opt.label}</p>
                <p className="text-xs text-muted mt-0.5">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* API keys */}
      {config.mode === "api_key" && (
        <div className="space-y-4">
          <div className="divider" />
          <div>
            <h2 className="text-base font-semibold text-primary">API Keys</h2>
            <p className="text-xs text-muted mt-1">
              Share a key with each trusted caller. They must send it as{" "}
              <code className="inline-code">Authorization: Bearer &lt;key&gt;</code> or{" "}
              <code className="inline-code">X-WAIL-Key: &lt;key&gt;</code>.
            </p>
          </div>

          {newKey && (
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
              <p className="text-xs font-semibold text-emerald-400 uppercase tracking-wider mb-3">
                New key — copy it now, shown once
              </p>
              <div className="flex items-center gap-3">
                <code className="code-block flex-1 text-xs">{newKey}</code>
                <CopyButton text={newKey} />
              </div>
            </div>
          )}

          {config.api_keys.length > 0 ? (
            <div className="card overflow-hidden">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {config.api_keys.map((key) => (
                    <tr key={key}>
                      <td>
                        <code className="font-mono text-xs text-cyan">{key}</code>
                      </td>
                      <td className="text-right">
                        <button onClick={() => revokeKey(key)} className="btn-danger py-1 px-2 text-xs">
                          Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-10 card text-center">
              <p className="text-sm text-muted">No keys yet.</p>
              <p className="text-xs text-dim mt-1">Generate a key below to get started.</p>
            </div>
          )}

          <button onClick={generateKey} className="btn-primary">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" />
            </svg>
            Generate new key
          </button>
        </div>
      )}

      {/* Allowlist */}
      {config.mode === "allowlist" && (
        <div className="space-y-4">
          <div className="divider" />
          <div>
            <h2 className="text-base font-semibold text-primary">Trusted contacts</h2>
            <p className="text-xs text-muted mt-1">
              Only these email addresses or phone numbers can book. The value must match the{" "}
              <code className="inline-code">customer_contact</code> field (case-insensitive).
            </p>
          </div>

          {config.allowlist.length > 0 ? (
            <div className="card overflow-hidden">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Contact</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {config.allowlist.map((contact) => (
                    <tr key={contact}>
                      <td className="font-mono text-sm text-primary">{contact}</td>
                      <td className="text-right">
                        <button onClick={() => removeContact(contact)} className="btn-danger py-1 px-2 text-xs">
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="alert-danger">
              <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" strokeLinecap="round" /><line x1="12" y1="16" x2="12.01" y2="16" strokeLinecap="round" />
              </svg>
              Allowlist is empty — no one can book while testing mode is off.
            </div>
          )}

          <div className="flex gap-2">
            <input
              className="input flex-1"
              value={allowInput}
              onChange={(e) => setAllowInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addContact() }}
              placeholder="email or phone number"
            />
            <button onClick={addContact} className="btn-ghost">Add</button>
          </div>
        </div>
      )}

      <div className="divider" />

      {/* Save */}
      <div className="flex items-center gap-4">
        <button onClick={save} disabled={saving} className="btn-primary">
          {saving ? (
            <>
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              Saving…
            </>
          ) : "Save trust settings"}
        </button>
        {saved && (
          <span className="flex items-center gap-1.5 text-sm text-emerald-400">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Saved
          </span>
        )}
        {error && <span className="text-sm text-red-400">{error}</span>}
      </div>
    </div>
  )
}
