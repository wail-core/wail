"""
WAIL Agent Evaluation Suite
============================
Spins up a Gemini agent (via Vertex AI) with WAIL tools, seeds a realistic
barber shop schedule into Google Calendar, then runs a set of eval prompts.

Uses the same OAuth credentials as the Calendar integration — no separate
API key needed. Connect Google Calendar via the dashboard first so
token.json exists, then re-connect once after the scope update to get the
cloud-platform scope included.

Prerequisites:
  1. WAIL backend running:     python example_usage.py
  2. Google Calendar connected via the dashboard (token.json must exist,
     and must include the cloud-platform scope — reconnect if needed)
  3. In .env:
       GOOGLE_CLOUD_PROJECT=your-project-id
       VERTEX_LOCATION=us-central1   (or your preferred region)

Usage:
  python evals.py                             # seed calendar + all evals
  python evals.py --no-seed                   # skip seeding
  python evals.py --cleanup                   # remove seeded events
  python evals.py --eval earliest_wednesday   # single eval by id
  python evals.py --model gemini-2.0-flash-001   # override model
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import httpx
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────

WAIL_BASE     = "http://localhost:8000"
SITE_ID       = "tonys-cuts"
DEFAULT_MODEL = "claude-haiku-4-5"
SEED_TAG      = "WAIL_EVAL_SEED"

HOURS = {
    "monday":    ("09:00", "19:00"),
    "tuesday":   ("09:00", "19:00"),
    "wednesday": ("09:00", "19:00"),
    "thursday":  ("09:00", "20:00"),
    "friday":    ("09:00", "20:00"),
    "saturday":  ("08:00", "18:00"),
}

# ── Calendar seed data ─────────────────────────────────────────────────
# Each entry: (title, start_time "HH:MM", duration_minutes, day_offset_from_monday)

SEED_APPOINTMENTS = [
    # Monday (offset 0)
    ("Appointment - Haircut",           "09:00", 30,  0),
    ("Appointment - Beard Trim",        "10:00", 20,  0),
    ("Appointment - Haircut + Beard",   "11:00", 50,  0),
    ("LUNCH BREAK",                     "13:00", 60,  0),
    ("Appointment - Hot Towel Shave",   "14:00", 40,  0),
    ("Appointment - Kids Cut",          "15:30", 20,  0),

    # Tuesday (offset 1)
    ("Appointment - Haircut",           "09:00", 30,  1),
    ("Appointment - Kids Cut",          "10:00", 20,  1),
    ("LUNCH BREAK",                     "13:00", 60,  1),
    ("Appointment - Haircut + Beard",   "14:30", 50,  1),

    # Wednesday (offset 2) — eval asks about earliest slot here
    ("Appointment - Beard Trim",        "09:00", 20,  2),
    ("Appointment - Haircut",           "10:00", 30,  2),
    ("Appointment - Hot Towel Shave",   "11:00", 40,  2),
    ("LUNCH BREAK",                     "13:00", 60,  2),
    ("Appointment - Haircut",           "14:00", 30,  2),
    ("Appointment - Haircut + Beard",   "15:00", 50,  2),

    # Thursday (offset 3)
    ("Appointment - Haircut",           "09:00", 30,  3),
    ("Appointment - Beard Trim",        "09:30", 20,  3),
    ("Appointment - Haircut",           "10:30", 30,  3),
    ("LUNCH BREAK",                     "13:00", 60,  3),
    ("Appointment - Kids Cut",          "14:00", 20,  3),
    ("Appointment - Hot Towel Shave",   "15:00", 40,  3),

    # Friday (offset 4)
    ("Appointment - Haircut",           "09:00", 30,  4),
    ("Appointment - Haircut + Beard",   "09:30", 50,  4),
    ("Appointment - Haircut",           "11:00", 30,  4),
    ("LUNCH BREAK",                     "13:00", 60,  4),
    ("Appointment - Beard Trim",        "14:00", 20,  4),
    ("Appointment - Haircut",           "15:00", 30,  4),
    ("Appointment - Hot Towel Shave",   "16:00", 40,  4),

    # Saturday (offset 5)
    ("Appointment - Haircut",           "08:00", 30,  5),
    ("Appointment - Beard Trim",        "09:00", 20,  5),
    ("Appointment - Haircut + Beard",   "10:00", 50,  5),
    ("Appointment - Haircut",           "11:30", 30,  5),
    ("LUNCH BREAK",                     "13:00", 60,  5),
    ("Appointment - Haircut",           "14:00", 30,  5),
    ("Appointment - Hot Towel Shave",   "15:00", 40,  5),
]

# ── Eval prompts ────────────────────────────────────────────────────────

EVALS = [
    {
        "id":     "earliest_wednesday",
        "prompt": (
            "What is the earliest available appointment slot at Tony's Cuts "
            "on the upcoming Wednesday? The shop is booked solid until its first gap."
        ),
        "hint":   "Expected: 09:20 (right after Beard Trim ends at 9:20)",
    },
    {
        "id":     "thursday_availability",
        "prompt": (
            "I want a haircut at Tony's Cuts this Thursday. "
            "Walk me through all the open gaps in the schedule that day."
        ),
        "hint":   "Expected: gaps at ~10:00, 11:00-13:00, 14:20-15:00, 15:40+ until 20:00",
    },
    {
        "id":     "latest_friday",
        "prompt": (
            "What is the latest time I could start an appointment at Tony's Cuts this Friday, "
            "given the shop closes at 20:00 and a standard haircut takes 30 minutes?"
        ),
        "hint":   "Expected: 19:30 (latest start for a 30-min service before 20:00 close)",
    },
    {
        "id":     "full_overview",
        "prompt": (
            "Give me a full overview of Tony's Cuts: location, hours, services with prices, "
            "and their barbers. Format it clearly."
        ),
        "hint":   "Expected: pulls from /sites/tonys-cuts, /services, /barbers",
    },
    {
        "id":     "booking_slots",
        "prompt": (
            "What haircut slots are available at Tony's Cuts this coming Wednesday? "
            "List all open times."
        ),
        "hint": (
            "Expected: agent fetches booking config (to get service id), "
            "then calls /booking/slots with date=next Wednesday and service_id=haircut, "
            "returns list of available HH:MM times."
        ),
    },
    {
        "id":     "book_appointment",
        "prompt": (
            "Book a beard trim at Tony's Cuts for a customer named Alex Rivera "
            "(email: alex.rivera@email.com, phone: 555-0101) at the earliest available slot "
            "this Thursday. Confirm the booking details."
        ),
        "hint": (
            "Expected: agent fetches /booking/config to learn required fields and service IDs, "
            "fetches /booking/slots for Thursday with service_id=beard-trim, "
            "picks the earliest slot, calls /booking/book with name/email/phone fields, "
            "returns confirmation message."
        ),
    },
    {
        "id":     "cancel_appointment",
        "prompt": (
            "Alex Rivera (email: alex.rivera@email.com) wants to cancel their most recent "
            "booking at Tony's Cuts. Find the booking and cancel it."
        ),
        "hint": (
            "Expected: agent fetches calendar events to find Alex's booking event_id, "
            "calls DELETE /booking/book/{event_id} with body {contact: 'alex.rivera@email.com'}, "
            "confirms cancellation."
        ),
    },
    {
        "id":     "group_booking",
        "prompt": (
            "Book a haircut at Tony's Cuts with the barber named 'Marcus' for Sam Chen "
            "(email: sam.chen@email.com) at the earliest available slot next Monday."
        ),
        "hint": (
            "Expected: agent fetches /booking/config and /groups, calls /booking/slots "
            "with service_id=haircut and group=Marcus, picks earliest slot, "
            "calls /booking/book with group=Marcus field."
        ),
    },
]

# ── Google Calendar seeding ─────────────────────────────────────────────

def _next_monday() -> datetime:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days_until_monday = (7 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_until_monday)


def _build_gcal_service():
    from integrations.google_calendar import google_calendar
    if not google_calendar.connected:
        print("ERROR: Google Calendar not connected. Connect via the dashboard first.")
        sys.exit(1)
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=google_calendar._creds)


def seed_calendar() -> None:
    print("Seeding Google Calendar with Tony's Cuts schedule...")
    service = _build_gcal_service()
    monday  = _next_monday()

    for title, start_str, duration, day_offset in SEED_APPOINTMENTS:
        h, m  = map(int, start_str.split(":"))
        start = monday + timedelta(days=day_offset, hours=h, minutes=m)
        end   = start + timedelta(minutes=duration)
        event = {
            "summary":     title,
            "description": f"Tony's Cuts | {SEED_TAG}",
            "start":       {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end":         {"dateTime": end.isoformat(),   "timeZone": "UTC"},
        }
        service.events().insert(calendarId="primary", body=event).execute()
        print(f"  Created: {start.strftime('%a %d %b %H:%M')} — {title}")

    print(f"Seeded {len(SEED_APPOINTMENTS)} events.\n")


def cleanup_calendar() -> None:
    print(f"Cleaning up seeded events (tag: {SEED_TAG})...")
    service = _build_gcal_service()
    monday  = _next_monday()
    sunday  = monday + timedelta(days=6, hours=23, minutes=59)

    result = service.events().list(
        calendarId="primary",
        timeMin=monday.isoformat(),
        timeMax=sunday.isoformat(),
        maxResults=200,
        singleEvents=True,
    ).execute()

    removed = 0
    for event in result.get("items", []):
        if SEED_TAG in event.get("description", ""):
            service.events().delete(calendarId="primary", eventId=event["id"]).execute()
            print(f"  Deleted: {event.get('summary', '?')}")
            removed += 1

    print(f"Removed {removed} events.\n")


# ── WAIL tool definition ───────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "call_wail_endpoint",
        "description": (
            "Call any WAIL API endpoint. "
            "Start with GET / for the manifest, or GET /sites/tonys-cuts/menu "
            "to discover all available endpoints for that site.\n\n"
            "CALENDAR — live booked appointments:\n"
            "  POST /integrations/google-calendar/events\n"
            "  Body: {calendar_id, days_ahead:{enabled,value}, max_events:{enabled,value}, "
            "filters:[{field,operator,value}], schema:{field:bool}}\n\n"
            "BOOKING — check availability and create/cancel appointments:\n"
            "  GET    /integrations/google-calendar/booking/config\n"
            "         → services list, hours, required booking fields, settings\n"
            "         Always fetch config first to learn valid service IDs and required fields.\n"
            "  POST   /integrations/google-calendar/booking/slots\n"
            "         Body: {date:'YYYY-MM-DD', service_id:'...', group:'WorkerName'(optional)}\n"
            "  POST   /integrations/google-calendar/booking/book\n"
            "         Body: {date:'YYYY-MM-DD', time:'HH:MM', service_id:'...',\n"
            "                group:'WorkerName'(optional), <field_id>:value, ...}\n"
            "         Field IDs come from config.fields — submit each as a top-level key.\n"
            "         Returns {status, event_id, confirmation, ...}\n"
            "  DELETE /integrations/google-calendar/booking/book/{event_id}\n"
            "         Body: {contact:'<trust-field value used when booking>'}\n"
            "         Only cancels WAIL-created bookings where the contact matches.\n\n"
            "GROUPS — per-worker schedule:\n"
            "  GET  /integrations/google-calendar/groups\n"
            "  POST /integrations/google-calendar/events/{group}\n\n"
            "CALENDARS — list available calendars:\n"
            "  GET  /integrations/google-calendar/calendars"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "API path, e.g. /sites/tonys-cuts/menu"},
                "method": {"type": "string", "enum": ["GET", "POST", "DELETE"], "description": "HTTP method. Default GET."},
                "body":   {"type": "object", "description": "JSON body for POST or DELETE requests."},
            },
            "required": ["path"],
        },
    },
]


def execute_tool(name: str, inputs: dict) -> str:
    if name != "call_wail_endpoint":
        return json.dumps({"error": f"Unknown tool: {name}"})

    path   = inputs.get("path", "")
    method = inputs.get("method", "GET").upper()
    body   = inputs.get("body")
    url    = f"{WAIL_BASE}{path}"

    try:
        if method == "POST":
            resp = httpx.post(url, json=body, timeout=15)
        elif method == "DELETE":
            resp = httpx.delete(url, json=body, timeout=15)
        else:
            resp = httpx.get(url, timeout=15)
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Claude agent loop ───────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are an AI assistant integrated with Tony's Cuts barbershop via WAIL
(Website Agent Integration Layer).

You have one tool: call_wail_endpoint. Use it to:
1. GET /sites/{SITE_ID}/menu — discover all available endpoints for this site.
2. POST /integrations/google-calendar/events — real booked appointments from Google Calendar.
   Set days_ahead to cover the upcoming week. Fetched events are BOOKED slots.
3. GET /integrations/google-calendar/booking/config — services, durations, prices, hours,
   AND the required booking fields (config.fields). Always fetch this first.
4. POST /integrations/google-calendar/booking/slots — available slots for a given date/service.
   Body: {{"date": "YYYY-MM-DD", "service_id": "...", "group": "WorkerName"(optional)}}
5. POST /integrations/google-calendar/booking/book — create an appointment.
   Body: {{"date": "YYYY-MM-DD", "time": "HH:MM", "service_id": "...", "group": "..."(optional),
          <field_id>: value, ...}}
   Submit every field from config.fields as a top-level key (e.g. name, email, phone, notes).
6. DELETE /integrations/google-calendar/booking/book/{{event_id}} — cancel a booking.
   Body: {{"contact": "<the trust-field value used when booking, e.g. email address>"}}
   This only works for WAIL-created bookings and the contact must match the booking record.
7. GET /integrations/google-calendar/groups — list configured worker groups.

When analysing availability:
- Use /booking/slots — it returns only open times, accounting for existing bookings and hours.
- For a specific worker, add "group": "WorkerName" to the slots and book bodies.
- Always fetch the booking config first to get valid service IDs and required fields.
- Reason step by step before booking.
"""


def run_eval(eval_case: dict, model_name: str) -> str:
    client   = anthropic.Anthropic()
    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": eval_case["prompt"]}
    ]

    print(f"\n{'='*70}")
    print(f"EVAL [{eval_case['id']}]  model={model_name}")
    print(f"  {eval_case['prompt']}")
    print(f"  Hint: {eval_case['hint']}")
    print("="*70)

    while True:
        response = client.messages.create(
            model=model_name,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        for block in response.content:
            if block.type == "text":
                print(f"\nAgent: {block.text}")

        if response.stop_reason == "end_turn":
            return next((b.text for b in response.content if b.type == "text"), "")

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"\n[Tool] {block.name}")
                    print(f"  path={block.input.get('path')}  method={block.input.get('method', 'GET')}")
                    result = execute_tool(block.name, block.input)
                    print(f"  → {result[:400]}{'...' if len(result) > 400 else ''}")
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})
        else:
            break

    return ""


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WAIL Gemini Evaluation Suite")
    parser.add_argument("--no-seed", action="store_true",  help="Skip seeding Google Calendar")
    parser.add_argument("--cleanup", action="store_true",  help="Remove seeded events and exit")
    parser.add_argument("--eval",    type=str,             help="Run a single eval by ID")
    parser.add_argument("--model",   type=str, default=DEFAULT_MODEL, help="Gemini model name")
    args = parser.parse_args()

    if args.cleanup:
        cleanup_calendar()
        return

    try:
        httpx.get(f"{WAIL_BASE}/", timeout=3)
    except Exception:
        print("ERROR: WAIL backend not reachable at http://localhost:8000")
        print("       Start it with: python example_usage.py")
        sys.exit(1)

    if not args.no_seed:
        seed_calendar()

    evals_to_run = EVALS
    if args.eval:
        evals_to_run = [e for e in EVALS if e["id"] == args.eval]
        if not evals_to_run:
            ids = [e["id"] for e in EVALS]
            print(f"Unknown eval id '{args.eval}'. Available: {ids}")
            sys.exit(1)

    results = {}
    for eval_case in evals_to_run:
        results[eval_case["id"]] = run_eval(eval_case, args.model)

    print(f"\n\n{'='*70}")
    print("EVAL SUMMARY")
    print("="*70)
    for eid, answer in results.items():
        hint = next(e["hint"] for e in EVALS if e["id"] == eid)
        print(f"\n[{eid}]")
        print(f"  Hint:   {hint}")
        print(f"  Answer: {answer[:300]}{'...' if len(answer) > 300 else ''}")

    print("\nDone. Run with --cleanup to remove seeded calendar events.")


if __name__ == "__main__":
    main()
