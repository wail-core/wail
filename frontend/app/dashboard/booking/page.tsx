"use client"

import { useEffect, useState } from "react"

const API  = "http://localhost:8000"
const DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

type DayHours = { open: string; close: string } | null

type Service = {
  _id:              string
  id:               string
  name:             string
  duration_minutes: number
  price:            number
}

type FieldConfig = {
  _id:      string
  id:       string
  label:    string
  required: boolean
}

type CalendarItem = { id: string; name: string; primary: boolean }

type BookingConfig = {
  enabled:               boolean
  calendar_id:           string
  timezone:              string
  slot_interval_minutes: number
  buffer_minutes:        number
  advance_booking_days:  number
  services:              Service[]
  hours:                 Record<string, DayHours>
  fields:                FieldConfig[]
  trust_field_id:        string
  confirmation_template: string
}

function uid() { return Math.random().toString(36).slice(2) }

function slugify(name: string) {
  return name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "")
}

const DEFAULT_FIELDS: Omit<FieldConfig, "_id">[] = [
  { id: "name",  label: "Full Name",               required: true  },
  { id: "email", label: "Email Address",            required: true  },
  { id: "phone", label: "Phone Number",             required: false },
  { id: "notes", label: "Notes / Special Requests", required: false },
]

const DEFAULT_CONFIG: BookingConfig = {
  enabled:               true,
  calendar_id:           "primary",
  timezone:              "UTC",
  slot_interval_minutes: 15,
  buffer_minutes:        0,
  advance_booking_days:  30,
  services: [],
  hours: {
    monday:    { open: "09:00", close: "19:00" },
    tuesday:   { open: "09:00", close: "19:00" },
    wednesday: { open: "09:00", close: "19:00" },
    thursday:  { open: "09:00", close: "20:00" },
    friday:    { open: "09:00", close: "20:00" },
    saturday:  { open: "08:00", close: "18:00" },
    sunday:    null,
  },
  fields:         DEFAULT_FIELDS.map((f) => ({ _id: uid(), ...f })),
  trust_field_id: "email",
  confirmation_template:
    "Appointment confirmed: {service} on {date} at {time} for {name}. We look forward to seeing you!",
}

function toPayload(cfg: BookingConfig) {
  return {
    ...cfg,
    services: cfg.services.map(({ _id, ...s }) => s),
    fields:   cfg.fields.map(({ _id, ...f }) => f),
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

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4 py-3 border-b border-white/[0.04] last:border-0">
      <div className="w-52 shrink-0 pt-2">
        <p className="text-sm text-muted">{label}</p>
        {hint && <p className="text-xs text-dim mt-0.5">{hint}</p>}
      </div>
      <div className="flex-1 pt-1">{children}</div>
    </div>
  )
}

export default function BookingPage() {
  const [config,    setConfig]    = useState<BookingConfig>(DEFAULT_CONFIG)
  const [saving,    setSaving]    = useState(false)
  const [saved,     setSaved]     = useState(false)
  const [error,     setError]     = useState("")
  const [loading,   setLoading]   = useState(true)
  const [calendars, setCalendars] = useState<CalendarItem[]>([])

  useEffect(() => {
    fetch(`${API}/integrations/google-calendar/booking/config`)
      .then((r) => r.json())
      .then((d) => {
        setConfig({
          ...DEFAULT_CONFIG,
          ...d,
          services: (d.services ?? []).map((s: Omit<Service, "_id">) => ({ _id: uid(), ...s })),
          fields:   (d.fields   ?? DEFAULT_FIELDS).map((f: Omit<FieldConfig, "_id">) => ({ _id: uid(), ...f })),
        })
      })
      .catch(() => {})
      .finally(() => setLoading(false))

    fetch(`${API}/integrations/google-calendar/calendars`)
      .then((r) => r.json())
      .then((d) => setCalendars(d.calendars ?? []))
      .catch(() => {})
  }, [])

  function set<K extends keyof BookingConfig>(key: K, value: BookingConfig[K]) {
    setConfig((c) => ({ ...c, [key]: value }))
    setSaved(false)
  }

  function addService() {
    set("services", [...config.services, { _id: uid(), id: "", name: "", duration_minutes: 30, price: 0 }])
  }

  function updateService(_id: string, patch: Partial<Service>) {
    set("services", config.services.map((s) => {
      if (s._id !== _id) return s
      const updated = { ...s, ...patch }
      if ("name" in patch && !patch.id) updated.id = slugify(updated.name)
      return updated
    }))
  }

  function removeService(_id: string) {
    set("services", config.services.filter((s) => s._id !== _id))
  }

  function setDayOpen(day: string, open: boolean) {
    set("hours", { ...config.hours, [day]: open ? { open: "09:00", close: "18:00" } : null })
  }

  function setDayTime(day: string, field: "open" | "close", value: string) {
    const current = config.hours[day]
    if (!current) return
    set("hours", { ...config.hours, [day]: { ...current, [field]: value } })
  }

  function addField() {
    set("fields", [...config.fields, { _id: uid(), id: "", label: "", required: false }])
  }

  function updateField(_id: string, patch: Partial<FieldConfig>) {
    set("fields", config.fields.map((f) => {
      if (f._id !== _id) return f
      const updated = { ...f, ...patch }
      if ("label" in patch && !patch.id) updated.id = slugify(updated.label)
      return updated
    }))
  }

  function removeField(_id: string) {
    const updated = config.fields.filter((f) => f._id !== _id)
    set("fields", updated)
    if (!updated.find((f) => f.id === config.trust_field_id)) {
      set("trust_field_id", updated[0]?.id ?? "")
    }
  }

  async function save() {
    setSaving(true)
    setError("")
    setSaved(false)
    try {
      const res  = await fetch(`${API}/integrations/google-calendar/booking/config`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(toPayload(config)),
      })
      const data = await res.json()
      if (data.error) setError(data.error)
      else setSaved(true)
    } catch { setError("Could not reach WAIL API.") }
    finally { setSaving(false) }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-muted text-sm animate-pulse">Loading…</div>
      </div>
    )
  }

  const fieldPlaceholders = [
    ...config.fields.map((f) => `{${f.id}}`),
    "{service}", "{date}", "{time}",
  ].join(", ")

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-primary tracking-tight">Booking</h1>
        <p className="text-muted text-sm mt-1">
          Configure services, availability, and confirmation templates for agent-powered booking.
        </p>
      </div>

      {/* Status */}
      <SectionCard title="Status">
        <label className="flex items-center gap-3 cursor-pointer">
          <div className="relative">
            <input type="checkbox" className="sr-only" checked={config.enabled} onChange={(e) => set("enabled", e.target.checked)} />
            <div className={`w-10 h-6 rounded-full transition-colors duration-200 ${config.enabled ? "bg-accent" : "bg-zinc-700"}`} />
            <div className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${config.enabled ? "translate-x-4" : "translate-x-0"}`} />
          </div>
          <div>
            <p className="text-sm font-medium text-primary">Booking enabled</p>
            <p className="text-xs text-muted">Agents can check slots and create appointments</p>
          </div>
        </label>
      </SectionCard>

      {/* Calendar & timing */}
      <SectionCard title="Calendar & timing" description="How agents discover and claim appointment slots.">
        <div className="space-y-0">
          <FieldRow label="Calendar" hint="Which calendar to read busy times from and write bookings to.">
            {calendars.length > 0 ? (
              <select className="select w-full" value={config.calendar_id} onChange={(e) => set("calendar_id", e.target.value)}>
                {calendars.map((cal) => (
                  <option key={cal.id} value={cal.id}>{cal.name}{cal.primary ? " (primary)" : ""}</option>
                ))}
              </select>
            ) : (
              <input className="input" value={config.calendar_id} onChange={(e) => set("calendar_id", e.target.value)} placeholder="primary" />
            )}
          </FieldRow>
          <FieldRow label="Timezone" hint="IANA timezone, e.g. America/New_York, Europe/London.">
            <input className="input" value={config.timezone} onChange={(e) => set("timezone", e.target.value)} placeholder="UTC" />
          </FieldRow>
          <FieldRow label="Slot interval (min)" hint="How often available slots are offered.">
            <input className="input w-24" type="number" min={5} max={120} step={5} value={config.slot_interval_minutes} onChange={(e) => set("slot_interval_minutes", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Buffer between appointments (min)" hint="Gap required between consecutive bookings.">
            <input className="input w-24" type="number" min={0} max={60} step={5} value={config.buffer_minutes} onChange={(e) => set("buffer_minutes", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Max advance booking (days)" hint="How far ahead customers can book.">
            <input className="input w-24" type="number" min={1} max={365} value={config.advance_booking_days} onChange={(e) => set("advance_booking_days", Number(e.target.value))} />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Services */}
      <SectionCard title="Services" description="Each service has a unique ID (used by agents), display name, duration, and optional price.">
        {config.services.length > 0 ? (
          <div className="overflow-hidden rounded-lg border border-white/[0.07] mb-4">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Duration (min)</th>
                  <th>Price ($)</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {config.services.map((svc) => (
                  <tr key={svc._id}>
                    <td><input className="input py-1.5 text-xs font-mono" value={svc.id} onChange={(e) => updateService(svc._id, { id: e.target.value })} placeholder="haircut" /></td>
                    <td><input className="input py-1.5 text-xs" value={svc.name} onChange={(e) => updateService(svc._id, { name: e.target.value })} placeholder="Haircut" /></td>
                    <td><input className="input w-20 py-1.5 text-xs" type="number" min={5} max={480} step={5} value={svc.duration_minutes} onChange={(e) => updateService(svc._id, { duration_minutes: Number(e.target.value) })} /></td>
                    <td><input className="input w-24 py-1.5 text-xs" type="number" min={0} step={0.5} value={svc.price} onChange={(e) => updateService(svc._id, { price: Number(e.target.value) })} /></td>
                    <td><button onClick={() => removeService(svc._id)} className="btn-danger py-1 px-2 text-xs">Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex flex-col items-center py-8 text-center border border-dashed border-white/[0.07] rounded-lg mb-4">
            <p className="text-sm text-muted">No services yet</p>
            <p className="text-xs text-dim mt-1">Add a service to let agents know what can be booked.</p>
          </div>
        )}
        <button onClick={addService} className="btn-ghost">
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" /></svg>
          Add service
        </button>
      </SectionCard>

      {/* Business hours */}
      <SectionCard title="Business hours" description="Set open/close times for each day. Uncheck a day to mark it as closed.">
        <div className="overflow-hidden rounded-lg border border-white/[0.07]">
          <table className="data-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Open</th>
                <th>Opens at</th>
                <th>Closes at</th>
              </tr>
            </thead>
            <tbody>
              {DAYS.map((day) => {
                const hours  = config.hours[day]
                const isOpen = !!hours
                return (
                  <tr key={day}>
                    <td className="capitalize font-medium">{day}</td>
                    <td>
                      <div className="relative inline-flex">
                        <input type="checkbox" className="sr-only peer" id={`day-${day}`} checked={isOpen} onChange={(e) => setDayOpen(day, e.target.checked)} />
                        <label htmlFor={`day-${day}`} className={`w-8 h-5 rounded-full cursor-pointer transition-colors duration-200 ${isOpen ? "bg-accent" : "bg-zinc-700"}`} />
                        <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white pointer-events-none transition-transform duration-200 ${isOpen ? "translate-x-3" : "translate-x-0"}`} />
                      </div>
                    </td>
                    <td>
                      {isOpen && (
                        <input type="time" className="input w-32 py-1.5 text-xs" value={hours!.open} onChange={(e) => setDayTime(day, "open", e.target.value)} />
                      )}
                    </td>
                    <td>
                      {isOpen && (
                        <input type="time" className="input w-32 py-1.5 text-xs" value={hours!.close} onChange={(e) => setDayTime(day, "close", e.target.value)} />
                      )}
                      {!isOpen && <span className="text-xs text-dim">Closed</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* Booking fields */}
      <SectionCard title="Booking fields" description="Fields agents must supply when calling /booking/book. Each field ID becomes a top-level key in the booking request body.">
        {config.fields.length > 0 && (
          <div className="overflow-hidden rounded-lg border border-white/[0.07] mb-4">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Field ID</th>
                  <th>Label (shown to customer)</th>
                  <th className="text-center">Required</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {config.fields.map((f) => (
                  <tr key={f._id}>
                    <td><input className="input py-1.5 text-xs font-mono" value={f.id} onChange={(e) => updateField(f._id, { id: e.target.value })} placeholder="email" /></td>
                    <td><input className="input py-1.5 text-xs" value={f.label} onChange={(e) => updateField(f._id, { label: e.target.value })} placeholder="Email Address" /></td>
                    <td className="text-center">
                      <input type="checkbox" className="checkbox" checked={f.required} onChange={(e) => updateField(f._id, { required: e.target.checked })} />
                    </td>
                    <td><button onClick={() => removeField(f._id)} className="btn-danger py-1 px-2 text-xs">Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center gap-4 flex-wrap">
          <button onClick={addField} className="btn-ghost">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" strokeLinecap="round" /><line x1="5" y1="12" x2="19" y2="12" strokeLinecap="round" /></svg>
            Add field
          </button>
          <div className="flex items-center gap-2 ml-auto">
            <label className="text-xs text-muted whitespace-nowrap">Trust field (allowlist):</label>
            <select className="select text-xs" value={config.trust_field_id} onChange={(e) => set("trust_field_id", e.target.value)}>
              {config.fields.map((f) => (
                <option key={f._id} value={f.id}>{f.label || f.id}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs text-dim mt-3">
          When trust mode is "Allowlist", this field's value is checked against the approved contacts list.
        </p>
      </SectionCard>

      {/* Confirmation template */}
      <SectionCard title="Confirmation message" description={`Template returned to the agent after a successful booking. Available placeholders: ${fieldPlaceholders}`}>
        <textarea
          className="textarea font-mono text-xs"
          rows={3}
          value={config.confirmation_template}
          onChange={(e) => set("confirmation_template", e.target.value)}
        />
      </SectionCard>

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
          ) : "Save booking config"}
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
