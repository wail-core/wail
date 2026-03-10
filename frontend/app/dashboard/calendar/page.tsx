"use client"

import { useEffect, useState, Suspense } from "react"
import { useSearchParams } from "next/navigation"

const API = "http://localhost:8000"

type Schema = Record<string, boolean>
type EnabledValue<T> = { enabled: boolean; value: T }

type GCalSettings = {
  calendar_id: string
  days_ahead:  EnabledValue<number>
  max_events:  EnabledValue<number>
}

type Filter = {
  _id:      string
  field:    string
  operator: string
  value:    string
}

type GroupMatch = {
  _id:      string
  field:    string
  operator: string
  value:    string
}

type Group = {
  _id:   string
  name:  string
  match: GroupMatch[]
}

type AllSettings = {
  gcal:    GCalSettings
  schema:  Schema
  filters: Filter[]
}

const DEFAULT_SCHEMA: Schema = {
  id:             true,
  title:          true,
  start:          true,
  end:            true,
  location:       true,
  status:         true,
  description:    false,
  attendee_count: false,
}

const DEFAULT_GCAL: GCalSettings = {
  calendar_id: "primary",
  days_ahead:  { enabled: true, value: 7  },
  max_events:  { enabled: true, value: 20 },
}

const DEFAULT_SETTINGS: AllSettings = {
  gcal:    DEFAULT_GCAL,
  schema:  DEFAULT_SCHEMA,
  filters: [],
}

const SCHEMA_FIELDS = [
  { key: "id",             label: "ID" },
  { key: "title",          label: "Title" },
  { key: "start",          label: "Start time" },
  { key: "end",            label: "End time" },
  { key: "location",       label: "Location" },
  { key: "status",         label: "Status" },
  { key: "description",    label: "Description" },
  { key: "attendee_count", label: "Attendee count" },
]

const FILTER_FIELDS = ["title", "start", "end", "location", "status", "description", "attendee_count", "id"]

const OPERATORS = [
  { value: "contains",     label: "contains"       },
  { value: "equals",       label: "equals"         },
  { value: "not_equals",   label: "not equals"     },
  { value: "starts_with",  label: "starts with"    },
  { value: "ends_with",    label: "ends with"      },
  { value: "greater_than", label: ">"              },
  { value: "less_than",    label: "<"              },
  { value: "exists",       label: "exists"         },
  { value: "not_exists",   label: "does not exist" },
]

const NO_VALUE_OPS = new Set(["exists", "not_exists"])

function uid() { return Math.random().toString(36).slice(2) }

function toPayload(s: AllSettings) {
  return {
    ...s.gcal,
    schema:  s.schema,
    filters: s.filters.map(({ _id, ...rest }) => rest),
  }
}

function SectionCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="card overflow-hidden">
      <div className="px-6 py-5 border-b border-white/[0.07]">
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
        {description && <p className="text-xs text-muted mt-0.5">{description}</p>}
      </div>
      <div className="p-6">{children}</div>
    </div>
  )
}

function StepBadge({ n, done }: { n: number; done: boolean }) {
  return (
    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${done ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-white/[0.07] text-muted border border-white/[0.07]"}`}>
      {done ? (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
          <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : n}
    </div>
  )
}

function FilterRow({ filter, onUpdate, onRemove }: { filter: Filter; onUpdate: (p: Partial<Filter>) => void; onRemove: () => void }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select className="select text-xs py-1.5" value={filter.field} onChange={(e) => onUpdate({ field: e.target.value })}>
        {FILTER_FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
      </select>
      <select className="select text-xs py-1.5" value={filter.operator} onChange={(e) => onUpdate({ operator: e.target.value })}>
        {OPERATORS.map((op) => <option key={op.value} value={op.value}>{op.label}</option>)}
      </select>
      {!NO_VALUE_OPS.has(filter.operator) && (
        <input className="input text-xs py-1.5 w-36" value={filter.value} onChange={(e) => onUpdate({ value: e.target.value })} placeholder="value" />
      )}
      <button onClick={onRemove} className="btn-danger py-1 px-2 text-xs">Remove</button>
    </div>
  )
}

function CalendarPageInner() {
  const searchParams = useSearchParams()

  const [configured, setConfigured] = useState(false)
  const [connected,  setConnected]  = useState(false)

  const [clientId,     setClientId]     = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [credSaving,   setCredSaving]   = useState(false)
  const [credError,    setCredError]    = useState("")

  const [settings, setSettings] = useState<AllSettings>(DEFAULT_SETTINGS)

  const [jsonOpen,  setJsonOpen]  = useState(false)
  const [jsonText,  setJsonText]  = useState("")
  const [jsonError, setJsonError] = useState("")

  const [groups,       setGroups]       = useState<Group[]>([])
  const [groupsSaving, setGroupsSaving] = useState(false)
  const [groupsError,  setGroupsError]  = useState("")
  const [groupsSaved,  setGroupsSaved]  = useState(false)

  const [events,       setEvents]       = useState<Record<string, unknown>[] | null>(null)
  const [previewError, setPreviewError] = useState("")
  const [loading,      setLoading]      = useState(false)

  useEffect(() => {
    fetch(`${API}/integrations/google-calendar/credentials`)
      .then((r) => r.json()).then((d) => setConfigured(d.configured)).catch(() => {})
    fetch(`${API}/integrations/google-calendar/status`)
      .then((r) => r.json()).then((d) => setConnected(d.connected)).catch(() => {})
    fetch(`${API}/integrations/google-calendar/groups`)
      .then((r) => r.json())
      .then((d) => {
        type RawGroup = Omit<Group, "_id"> & { match: Omit<GroupMatch, "_id">[] }
        setGroups((d.groups ?? []).map((g: RawGroup) => {
          const { match, ...rest } = g
          return {
            _id: uid(),
            ...rest,
            match: match.map((m) => Object.assign({ _id: uid() }, m) as GroupMatch),
          }
        }))
      })
      .catch(() => {})
  }, [searchParams])

  async function saveCredentials(e: React.FormEvent) {
    e.preventDefault()
    setCredSaving(true)
    setCredError("")
    try {
      const res  = await fetch(`${API}/integrations/google-calendar/credentials`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body:   JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
      })
      const data = await res.json()
      if (data.error) setCredError(data.error)
      else { setConfigured(true); setClientId(""); setClientSecret("") }
    } catch { setCredError("Could not reach WAIL API.") }
    finally { setCredSaving(false) }
  }

  function toggleSchema(field: string) {
    setSettings((s) => ({ ...s, schema: { ...s.schema, [field]: !s.schema[field] } }))
  }

  function setGcal(patch: Partial<GCalSettings>) {
    setSettings((s) => ({ ...s, gcal: { ...s.gcal, ...patch } }))
  }

  function setEV<K extends "days_ahead" | "max_events">(key: K, patch: Partial<EnabledValue<number>>) {
    setSettings((s) => ({ ...s, gcal: { ...s.gcal, [key]: { ...s.gcal[key], ...patch } } }))
  }

  function addFilter() {
    setSettings((s) => ({ ...s, filters: [...s.filters, { _id: uid(), field: "title", operator: "contains", value: "" }] }))
  }

  function updateFilter(_id: string, patch: Partial<Filter>) {
    setSettings((s) => ({ ...s, filters: s.filters.map((f) => (f._id === _id ? { ...f, ...patch } : f)) }))
  }

  function removeFilter(_id: string) {
    setSettings((s) => ({ ...s, filters: s.filters.filter((f) => f._id !== _id) }))
  }

  function addGroup() { setGroups((gs) => [...gs, { _id: uid(), name: "", match: [] }]); setGroupsSaved(false) }
  function updateGroup(_id: string, patch: Partial<Group>) { setGroups((gs) => gs.map((g) => (g._id === _id ? { ...g, ...patch } : g))); setGroupsSaved(false) }
  function removeGroup(_id: string) { setGroups((gs) => gs.filter((g) => g._id !== _id)); setGroupsSaved(false) }

  function addGroupMatch(groupId: string) {
    setGroups((gs) => gs.map((g) => g._id === groupId ? { ...g, match: [...g.match, { _id: uid(), field: "title", operator: "contains", value: "" }] } : g))
    setGroupsSaved(false)
  }

  function updateGroupMatch(groupId: string, matchId: string, patch: Partial<GroupMatch>) {
    setGroups((gs) => gs.map((g) => g._id === groupId ? { ...g, match: g.match.map((m) => (m._id === matchId ? { ...m, ...patch } : m)) } : g))
    setGroupsSaved(false)
  }

  function removeGroupMatch(groupId: string, matchId: string) {
    setGroups((gs) => gs.map((g) => g._id === groupId ? { ...g, match: g.match.filter((m) => m._id !== matchId) } : g))
    setGroupsSaved(false)
  }

  async function saveGroups() {
    setGroupsSaving(true); setGroupsError(""); setGroupsSaved(false)
    try {
      const payload = groups.map(({ _id: _gid, match, ...g }) => ({ ...g, match: match.map(({ _id: _mid, ...m }) => m) }))
      const res  = await fetch(`${API}/integrations/google-calendar/groups`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body:   JSON.stringify({ groups: payload }),
      })
      const data = await res.json()
      if (data.error) setGroupsError(data.error)
      else setGroupsSaved(true)
    } catch { setGroupsError("Could not reach WAIL API.") }
    finally { setGroupsSaving(false) }
  }

  function openJson() { setJsonText(JSON.stringify(toPayload(settings), null, 2)); setJsonError(""); setJsonOpen(true) }

  function applyJson() {
    try {
      const parsed = JSON.parse(jsonText)
      const { schema, filters, calendar_id, days_ahead, max_events, ...rest } = parsed
      setSettings({
        schema:  schema ?? settings.schema,
        filters: (filters ?? []).map((f: Omit<Filter, "_id">) => ({ _id: uid(), ...f })),
        gcal:    { calendar_id: calendar_id ?? settings.gcal.calendar_id, days_ahead: days_ahead ?? settings.gcal.days_ahead, max_events: max_events ?? settings.gcal.max_events, ...rest },
      })
      setJsonOpen(false); setJsonError("")
    } catch (err) { setJsonError(`Invalid JSON: ${(err as Error).message}`) }
  }

  async function preview() {
    setLoading(true); setPreviewError(""); setEvents(null)
    try {
      const res  = await fetch(`${API}/integrations/google-calendar/events`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body:   JSON.stringify(toPayload(settings)),
      })
      const data = await res.json()
      if (data.error) setPreviewError(data.error)
      else setEvents(data.events)
    } catch { setPreviewError("Could not reach WAIL API.") }
    finally { setLoading(false) }
  }

  async function disconnect() {
    await fetch(`${API}/integrations/google-calendar`, { method: "DELETE" })
    setConnected(false); setEvents(null)
  }

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-primary tracking-tight">Calendar</h1>
        <p className="text-muted text-sm mt-1">
          Configure what agents can read from your calendar. Schema and filters apply to any connected calendar service.
        </p>
      </div>

      {/* Step 1 — Credentials */}
      <div className="card overflow-hidden">
        <div className="flex items-center gap-4 px-6 py-5 border-b border-white/[0.07]">
          <StepBadge n={1} done={configured} />
          <div>
            <h2 className="text-sm font-semibold text-primary">Google OAuth credentials</h2>
            <p className="text-xs text-muted mt-0.5">
              Create a Web application OAuth 2.0 client at{" "}
              <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="text-accent hover:underline">
                Google Cloud Console
              </a>.
              Set redirect URI to <code className="inline-code">http://localhost:8000/auth/google/callback</code>.
            </p>
          </div>
        </div>
        <div className="p-6">
          {configured ? (
            <div className="flex items-center gap-3">
              <span className="badge-connected">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                Credentials saved
              </span>
              <button onClick={() => setConfigured(false)} className="btn-ghost text-xs py-1 px-2">Update</button>
            </div>
          ) : (
            <form onSubmit={saveCredentials} className="space-y-4 max-w-md">
              <div className="space-y-1.5">
                <label className="block text-xs font-medium text-muted uppercase tracking-wider">Client ID</label>
                <input className="input font-mono text-xs" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="xxxxxx.apps.googleusercontent.com" />
              </div>
              <div className="space-y-1.5">
                <label className="block text-xs font-medium text-muted uppercase tracking-wider">Client Secret</label>
                <input type="password" className="input font-mono text-xs" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder="GOCSPX-..." />
              </div>
              {credError && <div className="alert-danger py-2.5 text-xs">{credError}</div>}
              <button type="submit" disabled={credSaving} className="btn-primary">
                {credSaving ? (
                  <>
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    Saving…
                  </>
                ) : "Save credentials"}
              </button>
            </form>
          )}
        </div>
      </div>

      {/* Step 2 — Connect */}
      <div className="card overflow-hidden">
        <div className="flex items-center gap-4 px-6 py-5 border-b border-white/[0.07]">
          <StepBadge n={2} done={connected} />
          <div>
            <h2 className="text-sm font-semibold text-primary">Connect your Google account</h2>
            <p className="text-xs text-muted mt-0.5">Authorize WAIL to read your calendar data.</p>
          </div>
        </div>
        <div className="p-6">
          {!configured ? (
            <p className="text-sm text-dim">Complete step 1 first.</p>
          ) : connected ? (
            <div className="flex items-center gap-3">
              <span className="badge-connected">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Connected
              </span>
              <button onClick={disconnect} className="btn-danger text-xs py-1 px-2">Disconnect</button>
            </div>
          ) : (
            <a href={`${API}/auth/google`} className="btn-primary inline-flex">
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              Connect Google Calendar
            </a>
          )}
        </div>
      </div>

      {/* Step 3 — Configure */}
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <StepBadge n={3} done={false} />
          <h2 className="text-base font-semibold text-primary">Configure what agents see</h2>
        </div>

        {/* Schema */}
        <SectionCard title="Calendar schema" description="Universal — applies to any calendar service, not just Google.">
          <div className="grid grid-cols-2 gap-2">
            {SCHEMA_FIELDS.map(({ key, label }) => (
              <label key={key} className="flex items-center gap-2.5 p-2.5 rounded-lg hover:bg-white/[0.03] cursor-pointer transition-colors">
                <input type="checkbox" id={`s-${key}`} checked={!!settings.schema[key]} onChange={() => toggleSchema(key)} className="checkbox" />
                <span className="text-sm text-muted">{label}</span>
              </label>
            ))}
          </div>
        </SectionCard>

        {/* GCal fetch settings */}
        <SectionCard title="Google Calendar fetch settings">
          <div className="space-y-4">
            <div className="flex items-start gap-4 py-3 border-b border-white/[0.04]">
              <div className="w-48 shrink-0 pt-2">
                <p className="text-sm text-muted">Calendar ID</p>
                <p className="text-xs text-dim mt-0.5">Use "primary" or a specific calendar ID.</p>
              </div>
              <div className="flex-1 pt-1">
                <input className="input" value={settings.gcal.calendar_id} onChange={(e) => setGcal({ calendar_id: e.target.value })} placeholder="primary" />
              </div>
            </div>

            <div className="flex items-start gap-4 py-3 border-b border-white/[0.04]">
              <div className="w-48 shrink-0 pt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" className="checkbox" checked={settings.gcal.days_ahead.enabled} onChange={(e) => setEV("days_ahead", { enabled: e.target.checked })} />
                  <span className="text-sm text-muted">Days ahead</span>
                </label>
              </div>
              <div className="flex-1 flex items-center gap-3 pt-1">
                <input className="input w-24" type="number" value={settings.gcal.days_ahead.value} min={1} max={365} disabled={!settings.gcal.days_ahead.enabled} onChange={(e) => setEV("days_ahead", { value: Number(e.target.value) })} />
                <span className="text-xs text-dim">{settings.gcal.days_ahead.enabled ? "How many days forward to fetch." : "No time limit — fetches all future events."}</span>
              </div>
            </div>

            <div className="flex items-start gap-4 py-3">
              <div className="w-48 shrink-0 pt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" className="checkbox" checked={settings.gcal.max_events.enabled} onChange={(e) => setEV("max_events", { enabled: e.target.checked })} />
                  <span className="text-sm text-muted">Max events</span>
                </label>
              </div>
              <div className="flex-1 flex items-center gap-3 pt-1">
                <input className="input w-24" type="number" value={settings.gcal.max_events.value} min={1} max={2500} disabled={!settings.gcal.max_events.enabled} onChange={(e) => setEV("max_events", { value: Number(e.target.value) })} />
                <span className="text-xs text-dim">{settings.gcal.max_events.enabled ? "Maximum events returned per fetch." : "No limit — fetch all matching events."}</span>
              </div>
            </div>
          </div>
        </SectionCard>

        {/* Filters */}
        <SectionCard title="Filters" description="Filter on any field of the normalized event. All filters must match (AND). Applied after fetching.">
          <div className="space-y-2 mb-4">
            {settings.filters.map((f) => (
              <FilterRow
                key={f._id}
                filter={f}
                onUpdate={(patch) => updateFilter(f._id, patch)}
                onRemove={() => removeFilter(f._id)}
              />
            ))}
          </div>
          <button onClick={addFilter} className="btn-ghost">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" /></svg>
            Add filter
          </button>
        </SectionCard>

        {/* Groups */}
        <SectionCard title="Groups (workers / staff)" description="Assign events to named groups (e.g. one per barber). An event is placed in the first group whose rules all match.">
          <div className="space-y-4 mb-4">
            {groups.map((group) => (
              <div key={group._id} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 space-y-3">
                <div className="flex items-center gap-3">
                  <input
                    className="input flex-1"
                    value={group.name}
                    onChange={(e) => updateGroup(group._id, { name: e.target.value })}
                    placeholder="Group name (e.g. Tony)"
                  />
                  <button onClick={() => removeGroup(group._id)} className="btn-danger text-xs py-1 px-2">Remove</button>
                </div>

                {group.match.length > 0 && (
                  <div className="space-y-2 pl-1">
                    <p className="text-xs text-dim font-medium">Match rules (AND):</p>
                    {group.match.map((m) => (
                      <FilterRow
                        key={m._id}
                        filter={m}
                        onUpdate={(patch) => updateGroupMatch(group._id, m._id, patch)}
                        onRemove={() => removeGroupMatch(group._id, m._id)}
                      />
                    ))}
                  </div>
                )}

                <button onClick={() => addGroupMatch(group._id)} className="btn-ghost text-xs py-1 px-2">
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" /></svg>
                  Add match rule
                </button>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <button onClick={addGroup} className="btn-ghost">
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" /></svg>
              Add group
            </button>
            <button onClick={saveGroups} disabled={groupsSaving} className="btn-primary">
              {groupsSaving ? "Saving…" : "Save groups"}
            </button>
            {groupsSaved && (
              <span className="flex items-center gap-1.5 text-sm text-emerald-400">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Saved
              </span>
            )}
            {groupsError && <span className="text-sm text-red-400">{groupsError}</span>}
          </div>
        </SectionCard>

        {/* Actions */}
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={preview} disabled={!connected || loading} className="btn-primary">
            {loading ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Fetching…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" strokeLinecap="round" />
                </svg>
                Preview events
              </>
            )}
          </button>
          {!connected && <span className="text-xs text-dim">Connect Google Calendar first</span>}
          <button onClick={openJson} className="btn-ghost">Edit as JSON</button>
        </div>

        {previewError && <div className="alert-danger">{previewError}</div>}

        {/* JSON editor */}
        {jsonOpen && (
          <div className="card p-6 space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-primary">Edit settings as JSON</h3>
              <p className="text-xs text-muted mt-0.5">Full settings object. Changes here override the UI above.</p>
            </div>
            <textarea
              className="textarea font-mono text-xs"
              rows={20}
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
            />
            {jsonError && <div className="alert-danger py-2.5 text-xs">{jsonError}</div>}
            <div className="flex gap-3">
              <button onClick={applyJson} className="btn-primary">Apply</button>
              <button onClick={() => setJsonOpen(false)} className="btn-ghost">Cancel</button>
            </div>
          </div>
        )}

        {/* Preview results */}
        {events !== null && (
          <div className="card overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.07]">
              <h3 className="text-sm font-semibold text-primary">Preview</h3>
              <span className="text-xs text-muted bg-white/[0.05] px-2.5 py-1 rounded-full">{events.length} events</span>
            </div>
            {events.length === 0 ? (
              <div className="py-12 text-center">
                <p className="text-sm text-muted">No events found with these settings.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>{Object.keys(events[0]).map((col) => <th key={col}>{col}</th>)}</tr>
                  </thead>
                  <tbody>
                    {events.map((ev, i) => (
                      <tr key={i}>
                        {Object.values(ev).map((val, j) => (
                          <td key={j} className="font-mono text-xs text-muted">{String(val ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function CalendarPage() {
  return (
    <Suspense>
      <CalendarPageInner />
    </Suspense>
  )
}
