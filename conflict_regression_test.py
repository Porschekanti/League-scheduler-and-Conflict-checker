"""
Regression suite for the conflict engine.

Every case here is a bug that was live in the engine and is now fixed, or an
invariant that must survive future edits. Same style as smoke_test.py: a
linear script of asserts, no test framework, run it directly.

    rm -f scheduler.db scheduler.db-wal scheduler.db-shm
    python seed.py
    python conflict_regression_test.py

It reseeds the database itself, so it is safe to re-run.
"""
import os
import sqlite3
import subprocess
import sys
import threading
import time

for _f in ("scheduler.db", "scheduler.db-wal", "scheduler.db-shm"):
    if os.path.exists(_f):
        os.remove(_f)
subprocess.run([sys.executable, "seed.py"], check=True, capture_output=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.conflicts import (  # noqa: E402
    MatchValidationError,
    parse_instant,
    parse_interval,
)
from app.main import app  # noqa: E402

client = TestClient(app)
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"FAILED: {name} :: {detail}"
    PASSED.append(name)
    print(f"  ok  {name}")


def login(email, password):
    return client.post(
        "/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]


HEAD = {"Authorization": f"Bearer {login('head@example.edu', 'head-pass')}"}
BBALL = {"Authorization": f"Bearer {login('bball-rep@example.edu', 'rep-pass')}"}
CRICKET = {"Authorization": f"Bearer {login('cricket-rep@example.edu', 'rep-pass')}"}


def book(headers=None, **overrides):
    body = {
        "home_team_id": 1,
        "away_team_id": 2,
        "venue_id": 1,
        "sport": "Basketball",
        "season_id": 1,
        "start_time": "2026-10-01T18:00:00",
        "end_time": "2026-10-01T19:00:00",
    }
    body.update(overrides)
    return client.post("/schedules", headers=headers or BBALL, json=body)


print("\n=== Timestamps are compared as instants, not as strings ===")
check(
    "offset-aware and naive forms of the same moment are equal",
    parse_instant("2026-10-01T23:30:00+05:30") == parse_instant("2026-10-01T18:00:00"),
)
check(
    "trailing Z is accepted",
    parse_instant("2026-10-01T18:00:00Z") == parse_instant("2026-10-01T18:00:00"),
)
check(
    "a space separator is accepted",
    parse_instant("2026-10-01 18:00:00") == parse_instant("2026-10-01T18:00:00"),
)
try:
    parse_interval("2026-10-01T19:00:00", "2026-10-01T18:00:00")
    raise AssertionError("a backwards interval must be rejected")
except MatchValidationError:
    check("a backwards interval is rejected, not silently accepted", True)
try:
    parse_interval("tomorrow", "later")
    raise AssertionError("garbage must be rejected")
except MatchValidationError:
    check("an unparseable timestamp is rejected", True)

print("\n=== Roster setup: R101 plays Basketball (team 1) AND Cricket (team 3) ===")
client.post(
    "/players",
    headers=BBALL,
    json={"name": "Pashi", "roll_number": "R101", "team_id": 1,
          "sport": "Basketball", "season_id": 1},
)
client.post(
    "/players",
    headers=CRICKET,
    json={"name": "Pashi", "roll_number": "R101", "team_id": 3,
          "sport": "Cricket", "season_id": 2},
)
base = book()
check("baseline booking succeeds", base.status_code == 201, base.text)

print("\n=== Conflicts are found regardless of how the time was written ===")
r = book(start_time="2026-10-01T23:30:00+05:30", end_time="2026-10-02T00:30:00+05:30")
check("same instant expressed as +05:30 still collides", r.status_code == 409, r.text)
r = book(start_time="2026-10-01 18:30:00", end_time="2026-10-01 19:30:00")
check("space-separated overlapping time still collides", r.status_code == 409, r.text)
r = book(start_time="2026-10-01T19:00:00", end_time="2026-10-01T18:00:00")
check("reversed times cannot smuggle in a double-booking", r.status_code == 422, r.text)

print("\n=== The cross-league player conflict — the point of the project ===")
r = client.post(
    "/schedules",
    headers=CRICKET,
    json={"home_team_id": 3, "away_team_id": 4, "venue_id": 2, "sport": "Cricket",
          "season_id": 2, "start_time": "2026-10-01T18:15:00",
          "end_time": "2026-10-01T20:00:00"},
)
check("a player booked in Basketball cannot also play Cricket then",
      r.status_code == 409, r.text)
check("and the conflict names the player",
      any(c["type"] == "player" for c in r.json()["detail"]["conflicts"]), r.text)

print("\n=== Half-open intervals: back-to-back matches are legal ===")
r = book(start_time="2026-10-01T19:00:00", end_time="2026-10-01T20:00:00")
check("19:00-20:00 right after 18:00-19:00 is allowed", r.status_code == 201, r.text)

print("\n=== A malformed match is rejected, never reported as conflict-free ===")
for name, kw in [
    ("a team cannot play itself", dict(home_team_id=1, away_team_id=1)),
    ("a nonexistent team is rejected (not a 500)", dict(home_team_id=999)),
    ("a nonexistent venue is rejected (not a 500)", dict(venue_id=999)),
    ("garbage timestamps are rejected", dict(start_time="tomorrow", end_time="later")),
    ("teams from different sports cannot meet",
     dict(home_team_id=1, away_team_id=3, sport="Basketball")),
]:
    kw.setdefault("start_time", "2027-06-01T10:00:00")
    kw.setdefault("end_time", "2027-06-01T11:00:00")
    r = book(headers=HEAD, **kw)
    check(name, r.status_code == 422, f"got {r.status_code}: {r.text}")

print("\n=== Sport scope cannot be bypassed by mislabelling the payload ===")
r = book(headers=BBALL, home_team_id=3, away_team_id=4, venue_id=2, sport="Basketball",
         season_id=1, start_time="2027-07-01T10:00:00", end_time="2027-07-01T11:00:00")
check("a Basketball rep cannot schedule Cricket teams by claiming 'Basketball'",
      r.status_code in (403, 422), r.text)
r = book(headers=CRICKET, home_team_id=3, away_team_id=4, venue_id=2, sport="Cricket",
         season_id=2, start_time="2027-07-01T10:00:00", end_time="2027-07-01T11:00:00")
check("but the Cricket rep can book it correctly", r.status_code == 201, r.text)

print("\n=== Concurrent bookings of one slot: exactly one wins ===")
results = []


def racer():
    results.append(
        book(headers=HEAD, home_team_id=3, away_team_id=4, venue_id=2, sport="Cricket",
             season_id=2, start_time="2027-08-01T10:00:00",
             end_time="2027-08-01T11:00:00").status_code
    )


threads = [threading.Thread(target=racer) for _ in range(12)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check(f"12 concurrent identical bookings -> 1 created, 11 rejected (got {results})",
      results.count(201) == 1 and results.count(409) == 11)

db = sqlite3.connect("scheduler.db")
rows = db.execute(
    "SELECT COUNT(*) FROM matches WHERE venue_id = 2 AND start_time LIKE '2027-08-01%'"
).fetchone()[0]
check(f"and the database physically holds one row for that slot (got {rows})", rows == 1)

print("\n=== A failed generation job leaves nothing behind ===")
r = client.post(
    "/schedule-generations",
    headers=BBALL,
    json={"sport": "Basketball", "season_id": 1, "fixtures": [
        {"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
         "start_time": "2026-12-05T18:00:00", "end_time": "2026-12-05T19:00:00"},
        {"home_team_id": 1, "away_team_id": 2, "venue_id": 999,
         "start_time": "2026-12-06T18:00:00", "end_time": "2026-12-06T19:00:00"},
        {"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
         "start_time": "2026-12-07T18:00:00", "end_time": "2026-12-07T19:00:00"},
    ]},
)
job_id = r.json()["job_id"]
for _ in range(20):
    state = client.get(f"/schedule-generations/{job_id}").json()
    if state["status"] in ("COMPLETED", "FAILED"):
        break
    time.sleep(0.3)
claimed = state["result"]["created_draft_match_ids"]
db = sqlite3.connect("scheduler.db")
actual = db.execute("SELECT COUNT(*) FROM matches WHERE job_id = ?", (job_id,)).fetchone()[0]
check(f"job result ({len(claimed)} drafts) matches the database ({actual})",
      len(claimed) == actual)
check("the malformed fixture is reported, not silently dropped",
      any(c["type"] == "invalid"
          for s in state["result"]["skipped"] for c in s["conflicts"]),
      str(state))

print("\n=== Nothing in the database is invisible to the conflict engine ===")
from app.conflicts import find_unparseable_matches  # noqa: E402
from app.db import get_connection  # noqa: E402

conn = get_connection()
unparseable = find_unparseable_matches(conn)
check(f"no stored match has times the engine cannot compare (found {unparseable})",
      unparseable == [])
conn.close()

print(f"\n\n=== ALL {len(PASSED)} CONFLICT-ENGINE CHECKS PASSED ===")
