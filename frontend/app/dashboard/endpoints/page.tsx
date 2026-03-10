"use client"

import { useEffect, useState } from "react"

type Endpoint = {
  path:        string
  method:      string
  description: string
  tags:        string[]
  source:      "api" | "local"
}

const METHOD_COLORS: Record<string, string> = {
  GET:    "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  POST:   "text-blue-400 bg-blue-500/10 border-blue-500/20",
  PUT:    "text-amber-400 bg-amber-500/10 border-amber-500/20",
  PATCH:  "text-amber-400 bg-amber-500/10 border-amber-500/20",
  DELETE: "text-red-400 bg-red-500/10 border-red-500/20",
}

function MethodBadge({ method }: { method: string }) {
  const cls = METHOD_COLORS[method] ?? "text-muted bg-white/5 border-white/10"
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono font-medium border ${cls}`}>
      {method}
    </span>
  )
}

export default function EndpointsPage() {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([])
  const [apiError,  setApiError]  = useState("")

  const [method,      setMethod]      = useState("GET")
  const [path,        setPath]        = useState("")
  const [description, setDescription] = useState("")
  const [tags,        setTags]        = useState("")

  useEffect(() => {
    fetch("http://localhost:8000")
      .then((r) => r.json())
      .then((manifest) => {
        setEndpoints(
          manifest.endpoints.map((ep: Omit<Endpoint, "source">) => ({ ...ep, source: "api" as const }))
        )
      })
      .catch(() => setApiError("Could not reach WAIL API at http://localhost:8000"))
  }, [])

  function addEndpoint(e: React.FormEvent) {
    e.preventDefault()
    if (!path.trim()) return
    setEndpoints((prev) => [
      ...prev,
      {
        method:      method.trim().toUpperCase(),
        path:        path.trim(),
        description: description.trim(),
        tags:        tags.split(",").map((t) => t.trim()).filter(Boolean),
        source:      "local",
      },
    ])
    setPath("")
    setDescription("")
    setTags("")
    setMethod("GET")
  }

  function removeEndpoint(index: number) {
    setEndpoints((prev) => prev.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-primary tracking-tight">Endpoints</h1>
        <p className="text-muted text-sm mt-1">
          Manage the API endpoints exposed to AI agents. Changes are session-local — backend persistence coming soon.
        </p>
      </div>

      {apiError && (
        <div className="alert-warning">
          <svg className="w-4 h-4 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          {apiError}
        </div>
      )}

      {/* Add endpoint form */}
      <div className="card p-6">
        <h2 className="text-sm font-semibold text-primary mb-4">Add endpoint</h2>
        <form onSubmit={addEndpoint} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-muted uppercase tracking-wider">Method</label>
              <select
                className="select w-full"
                value={method}
                onChange={(e) => setMethod(e.target.value)}
              >
                {["GET","POST","PUT","PATCH","DELETE"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-muted uppercase tracking-wider">Path</label>
              <input
                className="input"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/sites/tonys-cuts/appointments"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-muted uppercase tracking-wider">Description</label>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this endpoint returns"
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-muted uppercase tracking-wider">Tags</label>
            <input
              className="input"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="observe, booking (comma-separated)"
            />
          </div>
          <div className="flex justify-end">
            <button type="submit" className="btn-primary">
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" />
                <line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" />
              </svg>
              Add endpoint
            </button>
          </div>
        </form>
      </div>

      {/* Endpoints table */}
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.07]">
          <h2 className="text-sm font-semibold text-primary">
            Registered endpoints
          </h2>
          <span className="text-xs text-muted bg-white/[0.05] px-2.5 py-1 rounded-full">
            {endpoints.length}
          </span>
        </div>

        {endpoints.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
            <div className="w-12 h-12 rounded-xl bg-white/[0.04] border border-white/[0.07] flex items-center justify-center mb-4">
              <svg className="w-5 h-5 text-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-muted">No endpoints yet</p>
            <p className="text-xs text-dim mt-1">Add your first endpoint above or connect a service.</p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Method</th>
                <th>Path</th>
                <th>Description</th>
                <th>Tags</th>
                <th>Source</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map((ep, i) => (
                <tr key={i}>
                  <td><MethodBadge method={ep.method} /></td>
                  <td>
                    <span className="font-mono text-xs text-cyan">{ep.path}</span>
                  </td>
                  <td className="text-muted">{ep.description}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {ep.tags.map((tag) => (
                        <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.05] text-muted border border-white/[0.07]">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${ep.source === "api" ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" : "text-indigo-400 bg-indigo-500/10 border-indigo-500/20"}`}>
                      {ep.source}
                    </span>
                  </td>
                  <td>
                    <button onClick={() => removeEndpoint(i)} className="btn-danger py-1 px-2 text-xs">
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
