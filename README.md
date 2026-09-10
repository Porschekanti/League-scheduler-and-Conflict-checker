# Intramural Scheduling System — Backend

FastAPI + SQLite3, matching the revised architecture: one modular monolith
(Auth+RBAC, Roster, Schedules, Job API modules), no Redis, optimistic
concurrency via a version check on publish, and an async worker for
schedule generation.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python seed.py                  # creates scheduler.db with sample data
uvicorn app.main:app --reload
```

Server runs at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

Seeded accounts (see console output from `seed.py` for IDs):
- `head@example.edu` / `head-pass` — Sports Head (global scope)
- `bball-rep@example.edu` / `rep-pass` — Sports Rep, scoped to Basketball
- `cricket-rep@example.edu` / `rep-pass` — Sports Rep, scoped to Cricket

## Project layout

```
app/
  db.py            SQLite schema + connection (WAL, foreign keys, busy timeout)
  security.py      Password hashing, JWT issue/verify
  deps.py          The three RBAC checks: auth -> role -> sport scope
  conflicts.py     Venue + cross-league player conflict detection
  worker.py        Async job processor (the "solver")
  routers/
    auth.py        POST /auth/login
    players.py     POST /players (roster + duplicate protection)
    schedules.py   Draft creation, validation, versioned publish, viewer read
    jobs.py        POST /schedule-generations, GET .../{jobId}
  main.py          App entrypoint
seed.py            Sample data for testing
smoke_test.py      End-to-end test covering every core behavior
```

## Running the frontend

The backend must be running first (see Setup above). Then just open
`frontend/index.html` directly in a browser — no build step, no server
needed for the frontend itself. CORS is already enabled on the backend for
this.

- **Left pane** — the public schedule, no sign-in required, refreshes every
  5 seconds. This is the same `GET /schedules` endpoint any client (web,
  Android, iOS) would render as a calendar.
- **Right pane** — sign in as one of the seeded accounts to get organizer
  tools: propose a match, add a roster player, trigger async schedule
  generation (watch it go `QUEUED` → `RUNNING` → `COMPLETED` in real time),
  and publish drafts.
- Try booking two overlapping matches at the same venue, or adding the same
  player to two teams in the same sport/season — the conflict messages
  you'll see are the actual `409` responses from the conflict engine.

**Known simplification:** there's no `GET` endpoint yet for "list my
drafts," so the frontend just tracks draft IDs/versions in memory as they're
created (from booking or from a completed generation job). Refreshing the
page loses that list — the drafts still exist in the database, you'd just
need to know their IDs. A real "my drafts" endpoint would be a natural
backend addition.

## Running the smoke test

```bash
pip install httpx  # or httpx2, depending on your starlette version
python smoke_test.py
```

Walks through: login, RBAC scope rejection, roster duplicate protection
(same sport/season blocked, different sport allowed), a clean booking, a
venue conflict, a **cross-league player conflict** (the core problem this
project addresses), publish with version check, a stale-version rejection,
the public read path, and the full async job -> poll -> completed flow.

## What's implemented vs. simplified

- **Conflict detection is real** — both venue and player checks query live
  data on every call, no caching, no stale copy.
- **Optimistic concurrency is real** — publish is a single transaction with
  a version check; a concurrent conflicting publish gets `409`, not silently
  overwritten.
- **The "solver" is the generate -> filter design from earlier in the
  project**, not a full OR-Tools constraint solver: given a list of
  candidate fixtures, it keeps the conflict-free ones as drafts and reports
  exactly why the rest were skipped. Swapping in a real optimizer later
  would only mean rewriting `worker.py` — the API contract (`202` + job ID
  + poll) doesn't need to change.
- **No native mobile clients** — this is the shared backend API only. Any
  client (web, Android, iOS) talks to the same JSON endpoints.
