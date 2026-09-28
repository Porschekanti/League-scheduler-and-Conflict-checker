# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

An **intramural sports league scheduler and conflict checker** for a college
sports department. It is a course project for a software design practical,
implemented as a **modular monolith**: FastAPI + SQLite3, no Redis, no
external queue, no ORM.

The problem it exists to solve: multiple sports (Basketball, Cricket, …) are
each scheduled independently by their own Sports Rep, so nobody notices that
*the same student* is rostered in two leagues and has been booked into two
matches at the same time. Venue double-booking is the second, more obvious
failure. **Everything in `app/conflicts.py` exists to catch those two cases.**

Repo: https://github.com/Porschekanti/League-scheduler-and-Conflict-checker

## Environment gotcha — read this first

The working directory name **ends in a trailing space**:

```
/Users/varun/Developer/software design practical /
```

Always quote paths. `cd /Users/varun/Developer/software\ design\ practical` (no
trailing space) fails with "no such file or directory". Use:

```bash
cd "/Users/varun/Developer/software design practical "
```

`app/` is not an installed package, so scripts run from anywhere else need
`PYTHONPATH` set to the repo root.

## Commands

```bash
# one-time setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt httpx

# reset + seed the database (destructive — drops all scheduling data)
rm -f scheduler.db scheduler.db-wal scheduler.db-shm && .venv/bin/python seed.py

# run the API
.venv/bin/uvicorn app.main:app --reload     # http://localhost:8000, docs at /docs

# end-to-end behavioural test (needs a freshly seeded DB; not idempotent)
.venv/bin/python smoke_test.py

# conflict-engine regression suite (reseeds itself, safe to re-run)
.venv/bin/python conflict_regression_test.py

# trimester seasons, blackout, and master-sheet suite (reseeds itself)
.venv/bin/python booking_rules_test.py

# frontend: no build step — open frontend/index.html directly in a browser
# with the backend already running. CORS is wide open for exactly this.
```

`smoke_test.py` uses `fastapi.testclient.TestClient`, so it does **not** need
a running server — but it does need `httpx` installed and a seeded
`scheduler.db`. It asserts on hardcoded seeded IDs (teams 1/2/3, venues 1/2,
seasons 1/2) and on absolute row IDs, so **re-running it without re-seeding
will fail**. Always reseed first.

## Architecture

```
app/
  main.py          FastAPI app, permissive CORS, init_db() on startup
  db.py            Schema (DDL as one string) + per-request connection factory
  security.py      PBKDF2-SHA256 password hashing, HS256 JWT issue/verify
  deps.py          The three-layer RBAC check
  conflicts.py     ** the conflict engine — the heart of the project **
  worker.py        Background "solver": generate -> filter -> persist drafts
  routers/
    auth.py        POST /auth/login
    players.py     POST /players (roster; duplicate rule enforced by DB)
    schedules.py   POST /schedules, PATCH /schedules/{id},
                   POST /schedules/{id}/publish, POST /schedules/{id}/cancel,
                   GET /schedules
    jobs.py        POST /schedule-generations, GET /schedule-generations/{id}
    teams.py       POST/GET /teams, GET /venues, GET /seasons,
                   GET /teams/{id}/roster  (reference data for clients)
    role_assignments.py  Nominate / accept / revoke HEAD and REP roles
    master_sheet.py  GET /master-sheet/status, POST /master-sheet/sync
  trimesters.py    Date -> academic trimester -> season (find-or-create)
  sheets.py        Master-sheet adapter: CSV and Google backends
seed.py            Sample users/seasons/teams/venues
smoke_test.py      Happy-path + core-rule walkthrough
conflict_regression_test.py  Conflict-engine regression suite
booking_rules_test.py        Trimesters, blackout, and master-sheet suite
frontend/index.html  Single-file vanilla-JS client, no dependencies
```

There is no migration tool. `init_db()` runs `CREATE TABLE IF NOT EXISTS`
only, so **changing a column type or constraint requires deleting
`scheduler.db` and reseeding** — an edited `SCHEMA` string will silently not
apply to an existing database file. This is the single most common way to get
confusing behaviour after a schema edit.

### Domain model

- `users` — `role` is `HEAD` | `REP` | `VIEWER`; `sport_scope` is the sport a
  REP is allowed to touch (NULL for HEAD, who is global).
- `players` — a person, unique by `roll_number`, independent of any sport.
- `teams` — belongs to exactly one `sport` + `season_id`.
- `team_members` — join row. `sport` and `season_id` are **deliberately
  denormalized** onto this table so `UNIQUE(player_id, sport, season_id)` can
  enforce "one student may not be on two teams in the same sport and season"
  at the database level. Do not "clean up" that denormalization — the
  constraint is the feature.
- `matches` — has `status` (`DRAFT`/`CONFIRMED`/`CANCELLED`) and an integer
  `version` used for optimistic concurrency on publish.
- `jobs` — async schedule-generation jobs, with JSON `payload` and `result`.
- `academic_terms` — a school year; `is_current` marks the live one.
- `role_assignments` — the succession ledger. A HEAD/REP post is nominated by
  one user for another and only takes effect on acceptance
  (`PENDING`/`ACCEPTED`/`REVOKED`). `seed.py` bootstraps the first HEAD with a
  pre-accepted row, since nobody exists yet to nominate them.

Note `jobs` must be declared **before** `matches` in `SCHEMA` — `matches.job_id`
references it, and `executescript` runs the DDL in order.

### The three RBAC layers (`deps.py`)

Applied in this order, and all three matter:

1. `get_current_user` — is the bearer token valid and does the user still exist?
2. `require_role("HEAD","REP")` — does this **role** permit the action at all?
3. `require_sport_scope(user, sport)` — does it apply to **this sport**? HEAD
   is exempt; a REP must match their `sport_scope`.

Layer 3 is a plain function call inside the handler, *not* a dependency,
because it needs the target sport, which is only known after reading the body
or the row. Every new write endpoint must call it explicitly — it is easy to
forget, and forgetting it is a silent authorization hole.

### Request lifecycle

`get_db()` yields a **new `sqlite3.Connection` per request** and closes it in
`finally`. FastAPI caches dependency results per request, so the handler and
`get_current_user` share the same connection. Connections use
`foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.

Because handlers are sync `def`, FastAPI runs them in a threadpool —
**multiple requests genuinely execute concurrently**. Any check-then-write
sequence needs a real transaction (`BEGIN IMMEDIATE`), not just adjacent
statements.

### The async job path

`POST /schedule-generations` inserts a `QUEUED` job, spawns a
`threading.Thread` running `worker.run_job`, and returns `202` immediately.
The worker opens **its own connection**, walks the candidate fixtures, runs
the same `validate_match` used everywhere else, inserts the clean ones as
`DRAFT`, and records the rest under `skipped` with their conflict reasons.
The client polls `GET /schedule-generations/{job_id}`.

This is a *generate-and-filter* design, not a constraint optimizer. Swapping
in OR-Tools later should only mean rewriting `worker.py`; the
`202 + job id + poll` contract is designed to stay.

In-process threads mean **jobs do not survive a restart** — a `RUNNING` job
whose process dies stays `RUNNING` forever. There is no reaper.

## Conflict engine invariants

`app/conflicts.py` is the core. When touching it, hold these invariants:

1. **`validate_match` is the single entry point.** `create_draft`,
   `validate_draft`, `publish_draft`, and `worker.run_job` all call it. Never
   reimplement a conflict rule at a call site — if the rule set changes it
   must change in one place.
2. **Always live data, never cached.** Every check queries current rows at
   call time. There is no in-memory copy of "the schedule". Introducing one
   would reintroduce exactly the stale-view bug the project exists to fix.
3. **Player conflicts are cross-sport and cross-season by design.** A player
   booked in a Basketball match cannot simultaneously be in a Cricket match.
   The `matches` query in `check_player_conflicts` must *not* be narrowed to
   the current sport or season — that narrowing would delete the project's
   headline feature.
4. **`CANCELLED` matches never conflict.** `DRAFT` matches *do* — a draft is a
   soft hold on the slot.
5. **`exclude_match_id` exists so a row does not conflict with itself** when
   re-validating an existing draft (the `PATCH` and `publish` paths).
6. **Overlap is half-open**: `[start, end)`. Back-to-back matches
   (`18:00–19:00` then `19:00–20:00`) must not conflict.
7. **Timestamps are compared as instants, not strings.** See below.
8. **"I found no conflicts" and "I could not check" must never be the same
   answer.** A malformed match raises `MatchValidationError` (mapped to
   `422`); it never returns an empty conflict list. Adding a new failure mode
   that returns `[]` instead of raising would reintroduce the worst class of
   bug this system can have — a missed clash that looks like a clean
   schedule.
9. **Check-then-write must be one transaction.** `validate_match` followed by
   an `INSERT`/`UPDATE` is only safe inside `BEGIN IMMEDIATE`; handlers run
   concurrently in a threadpool. The season find-or-create belongs inside that
   same transaction, or two first-of-the-trimester bookings race each other.
10. **The blackout is a welfare rule, not a resource rule.** It is about one
    body being asked to play twice, so a different venue does not excuse it and
    it spans every sport. `"player"` means double-booked; `"blackout"` means
    not enough recovery. Keep them as separate conflict types — they read
    completely differently to an organizer.
11. **The engine never makes a network call.** Master-sheet rows are mirrored
    into `external_bookings` and queried in SQL. A booking must not fail, or
    silently pass, because Google was slow.

### Timestamp handling — the sharp edge

`matches.start_time` / `end_time` are `TEXT`, and comparing TEXT
lexicographically is only correct if every value shares one format, precision
and timezone. Nothing in SQLite enforces that, and mixed forms
(`2026-10-01T23:30:00+05:30`, `2026-10-01 18:00:00`, a reversed interval) all
compare wrongly as strings — producing **missed conflicts**, the worst failure
this system can have.

Two mechanisms keep that closed, and both must stay:

- **Write path**: routers and the worker call `normalize_instant()` before
  inserting, so every stored value is canonical UTC
  (`2026-10-01T18:00:00+00:00`). This is also what makes `ORDER BY
  start_time` in `list_schedules` correct.
- **Read path**: the engine re-parses each row with `parse_instant()` and
  compares `datetime` objects, so it stays correct even against rows written
  before normalization existed.

Naive timestamps (no offset) are interpreted via `SCHEDULER_UTC_OFFSET_MINUTES`
(default `0`/UTC; set `330` for IST). One fixed assumption applied to all of
them keeps naive values mutually consistent.

`find_unparseable_matches(conn)` lists rows the engine cannot compare — rows
that are therefore invisible to conflict detection. It should always return
`[]`; a non-empty result means legacy data needs cleaning before the schedule
can be trusted.

Note the stored format changed with normalization: existing databases created
before it hold non-canonical strings. Those rows still validate correctly
(the read path is defensive) but will sort oddly in `GET /schedules`. Reseed,
or backfill, to get clean ordering.

### Seasons are derived, never entered

A season is one sport in one academic trimester, and it is worked out from the
match's own `start_time` rather than supplied by the client. `POST /schedules`,
the generation worker, `POST /teams` and `POST /players` all take no
`season_id`; a client that still sends one is ignored rather than rejected, so
older callers keep working.

`app/trimesters.py` owns the calendar:

| Trimester | Runs |
|-----------|------|
| T1 | 1 July – 24 September |
| T2 | 1 October – 24 December |
| T3 | 8 January – 21 April |

The academic year runs July → April, so T1/T2 sit in calendar year Y and T3 in
Y+1 — `Term.academic_year` is always **the year the academic year started**,
which is the part that is easy to get wrong. Dates in a gap (late September,
late December, early January, and the April–June summer break) are *not*
rejected: they attach to the trimester that just ended, with `in_break: true`
in the response.

`resolve_season` creates the season row on first use, so nobody sets seasons up
in advance. `UNIQUE(sport, academic_year, term_code)` is what keeps that safe
under concurrency.

Teams still carry a `season_id`, and a match is **not** forced to match its
teams' season — teams persist, matches are filed by date.

### The master sheet

`app/sheets.py` links the Google Sheet that people edit by hand. Rows are
mirrored into `external_bookings` (+ `external_booking_players`, matched on
roll number) and then checked by the ordinary conflict queries, producing
`venue_external`, `player_external` and `blackout_external` conflicts.

Configured entirely by environment; the default is off:

```bash
MASTER_SHEET_BACKEND=none|csv|google   # default none — every call is a no-op
MASTER_SHEET_CSV=/path/to/master.csv   # csv backend
MASTER_SHEET_ID=<spreadsheet id>       # google backend
MASTER_SHEET_TAB=Bookings
MASTER_SHEET_CREDENTIALS=/path/to/service-account.json
```

Sheet columns: `ref,venue,start,end,description,players` — `ref` is the row's
stable identity (sync upserts on it and deletes rows that vanish), `venue`
matches `venues.name`, and `players` is a `;`-separated list of roll numbers.

The Google backend needs `pip install gspread` plus a service account that the
sheet is shared with as Editor. **The sheet at the URL in the project brief is
not publicly readable — anonymous CSV export returns 401** — so the CSV backend
is what runs today and the Google one activates when credentials exist.

`POST /master-sheet/sync` is HEAD-only, because a sync changes what every sport
may book. `push_match` mirrors a confirmed match back and deliberately cannot
fail a publish: the booking is already committed here, and the response says
whether the push landed via `pushed_to_master_sheet`.

## Conventions

- Stdlib `sqlite3` with `?` placeholders everywhere. No ORM, no query builder,
  and **never** f-string SQL.
- `conn.row_factory = sqlite3.Row`; results are indexed like `row["column"]`.
  Convert with `dict(row)` before returning from a handler.
- Pydantic models for request bodies, declared next to the handler that uses
  them. No shared schemas module.
- Conflicts are returned as a list of dicts, each with a `"type"` key
  (`"venue"` or `"player"`) plus identifying fields, and surfaced as
  `409 {"detail": {"conflicts": [...]}}`. The frontend and `smoke_test.py`
  both destructure that exact shape — changing it breaks both.
- Comments in this codebase explain *why a design decision was made*, often
  referencing the design doc. Match that register; don't add narration of what
  the code already says.
- Errors that are a client's fault use `HTTPException` with a specific status.
  `409` means "a rule rejected this", `403` means "not your sport".

## Known gaps

Documented so they are not mistaken for bugs to fix incidentally, or for
solved problems:

- No `GET /schedules/drafts` ("list my drafts"). The frontend tracks draft
  IDs in memory only, so a page refresh loses the list. The drafts still
  exist in the DB.
- `JWT_SECRET` defaults to a hardcoded dev string; override with
  `SCHEDULER_JWT_SECRET`. There is no token refresh or revocation.
- CORS is `allow_origins=["*"]` for local development convenience.
- Generation jobs run as in-process threads: a `RUNNING` job whose process
  dies stays `RUNNING` forever, and there is no reaper to requeue it.
- Conflict queries have no time-window bound in SQL — they fetch every match
  at a venue (or for a player) and filter by overlap in Python. Indexes make
  the lookup cheap, but a date-range `WHERE` clause is the next win once the
  stored timestamps are known to be uniformly canonical.
- The blackout is a single global constant (`SCHEDULER_BLACKOUT_MINUTES`,
  default 90), not per-sport. Your example of a sport wanting a 60-minute gap
  would need a per-sport column.
- There is still no travel buffer between *venues* — the blackout covers player
  rest, but two different players' matches back-to-back at opposite ends of
  campus are fine by design.
- Master-sheet sync is manual (`POST /master-sheet/sync`); nothing polls the
  sheet, so a row added there is invisible until someone syncs.
- `push_match` only fires on publish, and a failed push is reported but not
  retried or queued.
- `VIEWER` is in the role CHECK constraint but no endpoint requires it;
  `GET /schedules` is deliberately unauthenticated.
- No rate limiting, no audit log, no pagination on `GET /schedules`.
- No test framework — `smoke_test.py` is a linear script of `assert`s.
- `cancel_match` in `schedules.py` calls `require_sport_scope` after
  `BEGIN IMMEDIATE` without rolling back, so a scope rejection holds the write
  lock until the connection closes — the same leak `publish_draft` wraps in
  try/except. Worth tidying when that file is next touched.
