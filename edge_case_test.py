"""
Edge-case suite — the awkward inputs, boundaries and races that the three
behavioural suites do not reach.

Everything here writes to a timestamped log under `logs/`, plus `logs/latest.log`
so the most recent run is always at a stable path. Each check records what was
expected and what actually happened, so a failure is diagnosable from the log
alone without re-running anything.

    python edge_case_test.py

Reseeds the database itself, so it is safe to re-run.

Where a check fails, that is reported as a FINDING rather than crashing the run:
the point of this file is to survey the whole surface in one pass, not to stop
at the first surprise.
"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
STAMP = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
LOG_PATH = LOG_DIR / f"edge-cases-{STAMP}.log"
LATEST = LOG_DIR / "latest.log"

_lines: list[str] = []
PASSED = 0
FAILED = 0
SECTION = ""


def log(line: str = "") -> None:
    print(line)
    _lines.append(line)


def section(name: str) -> None:
    global SECTION
    SECTION = name
    log("")
    log(f"=== {name} " + "=" * max(0, 66 - len(name)))


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        log(f"  PASS     {name}")
    else:
        FAILED += 1
        log(f"  FINDING  {name}")
        if detail:
            log(f"           {detail}")
    return ok


def flush() -> None:
    header = [
        "Intramural Scheduler — edge case run",
        f"started   : {STAMP}",
        f"python    : {sys.version.split()[0]}",
        f"database  : {os.environ.get('SCHEDULER_DB_PATH', 'scheduler.db')}",
        f"blackout  : {os.environ.get('SCHEDULER_BLACKOUT_MINUTES', '90')} min",
        f"utc offset: {os.environ.get('SCHEDULER_UTC_OFFSET_MINUTES', '0')} min",
        "",
    ]
    body = "\n".join(header + _lines) + "\n"
    LOG_PATH.write_text(body)
    LATEST.write_text(body)


# --------------------------------------------------------------------- setup

for f in ("scheduler.db", "scheduler.db-wal", "scheduler.db-shm"):
    if os.path.exists(f):
        os.remove(f)
subprocess.run([sys.executable, "seed.py"], check=True, capture_output=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.conflicts import (  # noqa: E402
    BLACKOUT_MINUTES, MatchValidationError, parse_instant, parse_interval,
)
from app.db import get_connection, set_actor  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.trimesters import resolve_term  # noqa: E402

client = TestClient(app)


def login(email: str, password: str) -> str | None:
    r = client.post("/auth/login", json={"email": email, "password": password})
    return r.json().get("access_token") if r.status_code == 200 else None


HEAD = {"Authorization": f"Bearer {login('head@example.edu', 'head-pass')}"}
BBALL = {"Authorization": f"Bearer {login('bball-rep@example.edu', 'rep-pass')}"}
CRICKET = {"Authorization": f"Bearer {login('cricket-rep@example.edu', 'rep-pass')}"}


def book(headers=None, **over):
    body = {
        "home_team_id": 1, "away_team_id": 2, "venue_id": 1, "sport": "Basketball",
        "start_time": "2027-03-01T18:00:00+00:00", "end_time": "2027-03-01T19:00:00+00:00",
    }
    body.update(over)
    try:
        return client.post("/schedules", headers=headers or HEAD, json=body)
    except Exception as exc:                       # an unhandled 500 is a finding
        class R:
            status_code = 500
            _m = f"{type(exc).__name__}: {exc}"
            def json(self): return {"UNHANDLED": self._m}
            @property
            def text(self): return self._m
        return R()


def publish(mid: int, version: int, headers=None):
    return client.post(f"/schedules/{mid}/publish", headers=headers or HEAD,
                       json={"expected_version": version})


def cancel(mid: int, version: int, headers=None):
    return client.post(f"/schedules/{mid}/cancel", headers=headers or HEAD,
                       json={"expected_version": version})


# =============================================================== time parsing

section("Timestamp parsing: formats that must be understood")
for label, value in [
    ("ISO with Z", "2027-03-01T18:00:00Z"),
    ("ISO with +05:30", "2027-03-01T23:30:00+05:30"),
    ("ISO with -04:00", "2027-03-01T14:00:00-04:00"),
    ("space separator", "2027-03-01 18:00:00"),
    ("minute precision", "2027-03-01T18:00"),
    ("microseconds", "2027-03-01T18:00:00.123456+00:00"),
]:
    try:
        parse_instant(value)
        check(f"{label} parses", True)
    except MatchValidationError as exc:
        check(f"{label} parses", False, f"{value!r} -> {exc}")

section("Timestamp parsing: nonsense that must be refused")
for label, value in [
    ("empty string", ""),
    ("whitespace only", "   "),
    ("plain words", "tomorrow"),
    ("date only", "not-a-date"),
    ("month 13", "2027-13-01T18:00:00"),
    ("day 32", "2027-03-32T18:00:00"),
    ("hour 25", "2027-03-01T25:00:00"),
    ("Feb 30", "2027-02-30T18:00:00"),
]:
    try:
        parse_instant(value)
        check(f"{label} is refused", False, f"{value!r} was accepted")
    except MatchValidationError:
        check(f"{label} is refused", True)

section("Interval sanity")
try:
    parse_interval("2027-03-01T19:00:00+00:00", "2027-03-01T18:00:00+00:00")
    check("reversed interval refused", False, "a backwards interval was accepted")
except MatchValidationError:
    check("reversed interval refused", True)

try:
    parse_interval("2027-03-01T18:00:00+00:00", "2027-03-01T18:00:00+00:00")
    check("zero-length interval refused", False, "start == end was accepted")
except MatchValidationError:
    check("zero-length interval refused", True)

# A zero-length match would overlap nothing and be overlapped by nothing, so it
# would sit in the schedule invisible to every conflict check.

section("Equivalent instants in different notations compare equal")
pairs = [
    ("2027-03-01T18:00:00Z", "2027-03-01T23:30:00+05:30"),
    ("2027-03-01T18:00:00+00:00", "2027-03-01 18:00:00"),
    ("2027-03-01T18:00", "2027-03-01T18:00:00"),
]
for a, b in pairs:
    check(f"{a}  ==  {b}", parse_instant(a) == parse_instant(b))

# =========================================================== trimester edges

section("Trimester boundaries, including the awkward ones")
for date_str, want_label, want_break in [
    ("2026-07-01", "2026-27 T1", False),   # first day of the academic year
    ("2026-09-24", "2026-27 T1", False),   # last teaching day of T1
    ("2026-09-25", "2026-27 T1", True),    # first gap day
    ("2026-09-30", "2026-27 T1", True),    # last gap day
    ("2026-10-01", "2026-27 T2", False),
    ("2026-12-24", "2026-27 T2", False),
    ("2026-12-25", "2026-27 T2", True),
    ("2026-12-31", "2026-27 T2", True),    # new year's eve
    ("2027-01-01", "2026-27 T2", True),    # new year's day, same academic year
    ("2027-01-07", "2026-27 T2", True),
    ("2027-01-08", "2026-27 T3", False),
    ("2027-04-21", "2026-27 T3", False),
    ("2027-04-22", "2026-27 T3", True),
    ("2027-06-30", "2026-27 T3", True),    # last day before the next year
    ("2027-07-01", "2027-28 T1", False),   # rollover
    ("2028-02-29", "2027-28 T3", False),   # leap day
]:
    from datetime import date as _date
    term = resolve_term(_date.fromisoformat(date_str))
    ok = term.label == want_label and term.in_break == want_break
    check(f"{date_str} -> {want_label}{' (break)' if want_break else ''}", ok,
          f"got {term.label} break={term.in_break}")

# ============================================================== booking edges

section("Malformed bookings are refused, never silently accepted")
for label, kw in [
    ("team plays itself", dict(home_team_id=1, away_team_id=1)),
    ("nonexistent home team", dict(home_team_id=99999)),
    ("nonexistent away team", dict(away_team_id=99999)),
    ("nonexistent venue", dict(venue_id=99999)),
    ("negative team id", dict(home_team_id=-1)),
    ("zero venue id", dict(venue_id=0)),
    ("teams from different sports", dict(home_team_id=1, away_team_id=3)),
    ("garbage times", dict(start_time="soon", end_time="later")),
    ("reversed times", dict(start_time="2027-03-01T19:00:00+00:00",
                            end_time="2027-03-01T18:00:00+00:00")),
    ("zero-length match", dict(start_time="2027-03-01T18:00:00+00:00",
                               end_time="2027-03-01T18:00:00+00:00")),
    ("empty sport", dict(sport="")),
]:
    kw.setdefault("start_time", "2027-05-01T10:00:00+00:00")
    kw.setdefault("end_time", "2027-05-01T11:00:00+00:00")
    r = book(**kw)
    check(f"{label} -> 4xx", 400 <= r.status_code < 500,
          f"got {r.status_code}: {str(r.json())[:150]}")

section("Odd but legal bookings are accepted")
r = book(start_time="2027-05-02T23:30:00+00:00", end_time="2027-05-03T00:30:00+00:00")
check("a match spanning midnight", r.status_code == 201, r.text[:160])
midnight_id = r.json().get("match_id") if r.status_code == 201 else None

r = book(venue_id=2, start_time="2027-05-04T06:00:00+00:00",
         end_time="2027-05-05T06:00:00+00:00")
check("a 24-hour match", r.status_code == 201, r.text[:160])

r = book(venue_id=2, start_time="2028-02-29T10:00:00+00:00",
         end_time="2028-02-29T11:00:00+00:00")
check("a match on a leap day", r.status_code == 201, r.text[:160])

section("Hostile strings are stored as data, not executed")
inject = "Rebels'); DROP TABLE matches;--"
r = client.post("/teams", headers=HEAD, json={"name": inject, "sport": "Basketball"})
check("SQL-injection-shaped team name is accepted as a literal", r.status_code == 201,
      r.text[:160])
conn = get_connection()
still_there = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
check("the matches table still exists afterwards", still_there is not None)
stored = conn.execute("SELECT name FROM teams WHERE name = ?", (inject,)).fetchone()
check("and the name round-trips byte for byte", stored is not None)
conn.close()

r = client.post("/teams", headers=HEAD, json={"name": "Ünïcødé Strîkers ⚽", "sport": "Cricket"})
check("unicode team name accepted", r.status_code == 201, r.text[:160])

r = client.post("/teams", headers=HEAD, json={"name": "x" * 5000, "sport": "Basketball"})
check("a 5000-character name does not 500", r.status_code < 500,
      f"got {r.status_code}")

# ================================================================= auth edges

section("Authentication")
r = client.get("/master-sheet/status")
check("no token -> 401", r.status_code == 401, f"got {r.status_code}")

r = client.get("/master-sheet/status", headers={"Authorization": "Bearer not.a.token"})
check("malformed token -> 401", r.status_code == 401, f"got {r.status_code}")

r = client.get("/master-sheet/status", headers={"Authorization": "head-pass"})
check("missing 'Bearer' prefix -> 401", r.status_code == 401, f"got {r.status_code}")

import jwt as _jwt  # noqa: E402
from app.security import JWT_ALGO, JWT_SECRET  # noqa: E402

expired = _jwt.encode(
    {"sub": "1", "role": "HEAD", "sport_scope": None,
     "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
    JWT_SECRET, algorithm=JWT_ALGO)
r = client.get("/master-sheet/status", headers={"Authorization": f"Bearer {expired}"})
check("expired token -> 401", r.status_code == 401, f"got {r.status_code}")

forged = _jwt.encode({"sub": "1", "role": "HEAD", "sport_scope": None,
                      "exp": int(time.time()) + 3600}, "wrong-secret", algorithm=JWT_ALGO)
r = client.get("/master-sheet/status", headers={"Authorization": f"Bearer {forged}"})
check("token signed with the wrong secret -> 401", r.status_code == 401, f"got {r.status_code}")

ghost = _jwt.encode({"sub": "999999", "role": "HEAD", "sport_scope": None,
                     "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALGO)
r = client.get("/master-sheet/status", headers={"Authorization": f"Bearer {ghost}"})
check("token for a user who no longer exists -> 401", r.status_code == 401,
      f"got {r.status_code}")

r = client.post("/auth/login", json={"email": "head@example.edu", "password": "wrong"})
check("wrong password -> 401", r.status_code == 401, f"got {r.status_code}")
r2 = client.post("/auth/login", json={"email": "nobody@example.edu", "password": "wrong"})
check("unknown user gives the SAME error as a wrong password",
      r.status_code == r2.status_code and r.json() == r2.json(),
      "login must not reveal which half was wrong")

section("Role and sport scope")
r = book(headers=CRICKET, home_team_id=1, away_team_id=2, sport="Basketball")
check("cricket rep cannot book Basketball -> 403", r.status_code == 403,
      f"got {r.status_code}")

r = book(headers=BBALL, home_team_id=3, away_team_id=4, venue_id=2, sport="Basketball",
         start_time="2027-05-06T10:00:00+00:00", end_time="2027-05-06T11:00:00+00:00")
check("basketball rep cannot smuggle Cricket teams in under its own sport label",
      400 <= r.status_code < 500, f"got {r.status_code}: {str(r.json())[:140]}")

conn = get_connection(); set_actor(conn, "SYSTEM")
conn.execute("INSERT INTO users (name,email,password_hash,role,sport_scope) "
             "VALUES (?,?,?,'VIEWER',NULL)",
             ("Spectator", "viewer@example.edu", hash_password("pw")))
conn.commit(); conn.close()
VIEWER = {"Authorization": f"Bearer {login('viewer@example.edu', 'pw')}"}
r = book(headers=VIEWER)
check("a VIEWER cannot book -> 403", r.status_code == 403, f"got {r.status_code}")
r = client.post("/master-sheet/sync", headers=BBALL)
check("a REP cannot run a master-sheet sync -> 403", r.status_code == 403,
      f"got {r.status_code}")

# ============================================================ cancel bookings

section("Cancelling bookings")
r = book(venue_id=1, start_time="2027-06-01T10:00:00+00:00",
         end_time="2027-06-01T11:00:00+00:00")
check("set-up booking created", r.status_code == 201, r.text[:160])
cancel_id = r.json()["match_id"]

r = publish(cancel_id, 1)
check("published to CONFIRMED", r.status_code == 200, r.text[:160])
published_version = r.json()["version"]

r = book(venue_id=1, start_time="2027-06-01T10:30:00+00:00",
         end_time="2027-06-01T11:30:00+00:00")
check("the slot is genuinely held while confirmed", r.status_code == 409,
      f"got {r.status_code}")

r = cancel(cancel_id, 999)
check("cancel with a stale version -> 409", r.status_code == 409, f"got {r.status_code}")

r = cancel(999999, 1)
check("cancel a match that does not exist -> 404", r.status_code == 404,
      f"got {r.status_code}")

r = cancel(cancel_id, published_version, headers=CRICKET)
check("cancel outside your sport scope -> 403", r.status_code == 403,
      f"got {r.status_code}")

r = cancel(cancel_id, published_version, headers=VIEWER)
check("a VIEWER cannot cancel -> 403", r.status_code == 403, f"got {r.status_code}")

r = cancel(cancel_id, published_version)
check("the owning role CAN cancel a confirmed match", r.status_code == 200,
      r.text[:160])

r = cancel(cancel_id, published_version + 1)
check("cancelling twice -> 409", r.status_code == 409, f"got {r.status_code}")

r = client.get("/schedules?season=active")
gone = all(m["id"] != cancel_id for m in r.json())
check("a cancelled match leaves the public schedule", gone)

r = book(venue_id=1, start_time="2027-06-01T10:30:00+00:00",
         end_time="2027-06-01T11:30:00+00:00")
check("and its venue slot is immediately bookable again", r.status_code == 201,
      r.text[:160])
if r.status_code == 201:
    cancel(r.json()["match_id"], 1)       # tidy up so later checks start clean

section("A cancelled match stops holding its players, including for rest")
conn = get_connection(); set_actor(conn, "SYSTEM")
conn.execute("INSERT INTO players (name, roll_number, status) VALUES ('Rest Tester','RT-1','ACTIVE')")
pid = conn.execute("SELECT id FROM players WHERE roll_number='RT-1'").fetchone()["id"]
season = conn.execute("SELECT season_id FROM teams WHERE id = 1").fetchone()["season_id"]
conn.execute("INSERT INTO team_members (team_id, player_id, sport, season_id, joined_at) "
             "VALUES (1, ?, 'Basketball', ?, datetime('now'))", (pid, season))
conn.commit(); conn.close()

r = book(venue_id=2, start_time="2027-07-10T10:00:00+00:00",
         end_time="2027-07-10T11:00:00+00:00")
rest_id = r.json()["match_id"] if r.status_code == 201 else None
check("a match holding the player exists", rest_id is not None, r.text[:160])

r = book(venue_id=1, start_time="2027-07-10T11:30:00+00:00",
         end_time="2027-07-10T12:30:00+00:00")
blocked_by_rest = r.status_code == 409 and any(
    c["type"] == "blackout" for c in r.json().get("detail", {}).get("conflicts", []))
check(f"a match {BLACKOUT_MINUTES // 3} min later is blocked by the rest rule",
      blocked_by_rest, f"got {r.status_code}: {str(r.json())[:160]}")

cancel(rest_id, 1)
r = book(venue_id=1, start_time="2027-07-10T11:30:00+00:00",
         end_time="2027-07-10T12:30:00+00:00")
check("after cancelling, the same booking is allowed", r.status_code == 201,
      f"got {r.status_code}: {str(r.json())[:160]}")

# ========================================================== blackout boundary

section(f"Blackout boundary ({BLACKOUT_MINUTES} min), to the minute")
base_start = datetime(2027, 8, 1, 10, 0, tzinfo=timezone.utc)
base_end = base_start + timedelta(hours=1)
r = book(venue_id=2, start_time=base_start.isoformat(), end_time=base_end.isoformat())
anchor = r.json()["match_id"] if r.status_code == 201 else None
check("anchor match booked", anchor is not None, r.text[:160])

for offset, expect_ok in [
    (BLACKOUT_MINUTES - 1, False),
    (BLACKOUT_MINUTES, True),
    (BLACKOUT_MINUTES + 1, True),
]:
    s = base_end + timedelta(minutes=offset)
    r = book(venue_id=1, start_time=s.isoformat(),
             end_time=(s + timedelta(hours=1)).isoformat())
    got_ok = r.status_code == 201
    check(f"{offset} min of rest -> {'allowed' if expect_ok else 'refused'}",
          got_ok == expect_ok, f"got {r.status_code}")
    if got_ok:
        cancel(r.json()["match_id"], 1)

# The rule is symmetric: a match placed BEFORE an existing one needs the gap too.
s = base_start - timedelta(minutes=BLACKOUT_MINUTES - 1) - timedelta(hours=1)
r = book(venue_id=1, start_time=s.isoformat(), end_time=(s + timedelta(hours=1)).isoformat())
check("too little rest BEFORE an existing match is refused too", r.status_code == 409,
      f"got {r.status_code}")
if anchor:
    cancel(anchor, 1)

# ================================================================ concurrency

section("Concurrency")
r = book(venue_id=3 if False else 1, start_time="2027-09-01T10:00:00+00:00",
         end_time="2027-09-01T11:00:00+00:00")
race_draft = r.json()["match_id"] if r.status_code == 201 else None
results: list[int] = []


def racer_publish():
    results.append(publish(race_draft, 1).status_code)


threads = [threading.Thread(target=racer_publish) for _ in range(8)]
for t in threads: t.start()
for t in threads: t.join()
check(f"8 concurrent publishes of one draft -> exactly 1 success (got {results.count(200)})",
      results.count(200) == 1, str(sorted(results)))

cancel_results: list[int] = []
ver = 2


def racer_cancel():
    cancel_results.append(cancel(race_draft, ver).status_code)


threads = [threading.Thread(target=racer_cancel) for _ in range(6)]
for t in threads: t.start()
for t in threads: t.join()
check(f"6 concurrent cancels -> exactly 1 success (got {cancel_results.count(200)})",
      cancel_results.count(200) == 1, str(sorted(cancel_results)))

bookings: list[int] = []


def racer_book():
    bookings.append(book(venue_id=2, start_time="2027-09-02T10:00:00+00:00",
                         end_time="2027-09-02T11:00:00+00:00").status_code)


threads = [threading.Thread(target=racer_book) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()
check(f"10 concurrent bookings of one free slot -> exactly 1 created "
      f"(got {bookings.count(201)})", bookings.count(201) == 1, str(sorted(bookings)))

# ============================================================== job edge cases

section("Generation jobs")
r = client.post("/schedule-generations", headers=HEAD,
                json={"sport": "Basketball", "fixtures": []})
check("an empty fixture list is accepted and completes", r.status_code == 202,
      f"got {r.status_code}")
if r.status_code == 202:
    jid = r.json()["job_id"]
    for _ in range(25):
        st = client.get(f"/schedule-generations/{jid}", headers=HEAD).json()
        if st.get("status") in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.3)
    check("empty job reaches COMPLETED, not FAILED", st.get("status") == "COMPLETED",
          str(st)[:160])

r = client.get("/schedule-generations/999999", headers=HEAD)
check("polling a job that does not exist -> 404", r.status_code == 404,
      f"got {r.status_code}")

r = client.get("/schedule-generations/1")
check("job status requires authentication", r.status_code == 401, f"got {r.status_code}")

# ============================================================ roster edges

section("Roster rules")
r = client.post("/players", headers=HEAD,
                json={"name": "Dup Tester", "roll_number": "DUP-1", "team_id": 1})
check("player added to a Basketball team", r.status_code == 201, r.text[:160])
r = client.post("/players", headers=HEAD,
                json={"name": "Dup Tester", "roll_number": "DUP-1", "team_id": 2})
check("same player on a second Basketball team in the same trimester -> 409",
      r.status_code == 409, f"got {r.status_code}")
r = client.post("/players", headers=HEAD,
                json={"name": "Dup Tester", "roll_number": "DUP-1", "team_id": 3})
check("but the same player MAY join a Cricket team", r.status_code == 201,
      r.text[:160])
r = client.post("/players", headers=HEAD,
                json={"name": "Ghost", "roll_number": "G-1", "team_id": 999999})
check("adding to a team that does not exist -> 404", r.status_code == 404,
      f"got {r.status_code}")

# ====================================================== engine invariant check

section("Engine invariant: nothing in the database is invisible to it")
from app.conflicts import find_unparseable_matches  # noqa: E402
conn = get_connection()
bad = find_unparseable_matches(conn)
check(f"every stored match has comparable times (found {len(bad)} that do not)",
      bad == [], str(bad)[:300])
counts = conn.execute(
    "SELECT status, COUNT(*) c FROM matches GROUP BY status").fetchall()
log("           match rows by status: " + ", ".join(f"{r['status']}={r['c']}" for r in counts))
conn.close()

# ===================================================================== report

log("")
log("=" * 72)
log(f"  checks run : {PASSED + FAILED}")
log(f"  passed     : {PASSED}")
log(f"  findings   : {FAILED}")
log("=" * 72)
flush()
print(f"\nlog written to {LOG_PATH.relative_to(Path.cwd())}")
print(f"           and {LATEST.relative_to(Path.cwd())}")
sys.exit(1 if FAILED else 0)
