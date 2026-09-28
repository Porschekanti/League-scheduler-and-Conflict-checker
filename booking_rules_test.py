"""
Regression suite for the booking rules added on top of the conflict engine:
trimester-derived seasons, the blackout rest period, and the master-sheet link.

Same style as the other suites: a linear script of asserts, no framework.

    python booking_rules_test.py

Reseeds itself, so it is safe to re-run.
"""
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

for _f in ("scheduler.db", "scheduler.db-wal", "scheduler.db-shm"):
    if os.path.exists(_f):
        os.remove(_f)
subprocess.run([sys.executable, "seed.py"], check=True, capture_output=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.conflicts import BLACKOUT_MINUTES  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.main import app  # noqa: E402
from app.sheets import (  # noqa: E402
    CsvMasterSheet,
    NullMasterSheet,
    SheetUnavailable,
    booking_for_match,
    get_master_sheet,
    sync_from_sheet,
)
from app.trimesters import resolve_term  # noqa: E402

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


def book(**overrides):
    body = {
        "home_team_id": 1, "away_team_id": 2, "venue_id": 1, "sport": "Basketball",
        "start_time": "2026-10-01T18:00:00", "end_time": "2026-10-01T19:00:00",
    }
    body.update(overrides)
    return client.post("/schedules", headers=HEAD, json=body)


print("\n=== Trimester boundaries ===")
for d, expected, in_break in [
    ("2026-07-01", "2026-27 T1", False),   # first day of T1
    ("2026-09-24", "2026-27 T1", False),   # last week of September
    ("2026-09-28", "2026-27 T1", True),    # gap -> attaches to T1
    ("2026-10-01", "2026-27 T2", False),   # first week of October
    ("2026-12-24", "2026-27 T2", False),   # last week of December
    ("2027-01-03", "2026-27 T2", True),    # new calendar year, same academic year
    ("2027-01-08", "2026-27 T3", False),   # second week of January
    ("2027-04-21", "2026-27 T3", False),   # third week of April
    ("2027-06-15", "2026-27 T3", True),    # summer break
    ("2027-07-01", "2027-28 T1", False),   # next academic year begins
]:
    term = resolve_term(date.fromisoformat(d))
    check(f"{d} -> {expected}{' (break)' if in_break else ''}",
          term.label == expected and term.in_break == in_break,
          f"got {term.label} break={term.in_break}")

print("\n=== The season is derived from the match date, never entered ===")
r = book(start_time="2026-10-05T10:00:00", end_time="2026-10-05T11:00:00")
check("booking without a season_id succeeds", r.status_code == 201, r.text)
check("and reports the trimester it was filed under",
      r.json()["term"] == "2026-27 T2", r.text)
oct_season = r.json()["season_id"]

r = book(start_time="2027-02-10T10:00:00", end_time="2027-02-10T11:00:00")
check("a February match lands in T3, not the same season",
      r.json()["term"] == "2026-27 T3" and r.json()["season_id"] != oct_season, r.text)

r = book(start_time="2027-06-15T10:00:00", end_time="2027-06-15T11:00:00")
check("a summer-break match attaches to the trimester that just ended",
      r.json()["term"] == "2026-27 T3" and r.json()["in_break"] is True, r.text)

r = book(start_time="2027-07-05T10:00:00", end_time="2027-07-05T11:00:00")
check("July rolls over into the next academic year",
      r.json()["term"] == "2027-28 T1", r.text)

conn = get_connection()
rows = conn.execute(
    "SELECT sport, academic_year, term_code FROM seasons ORDER BY id"
).fetchall()
check(f"seasons were created on demand, one per sport+trimester ({len(rows)} rows)",
      len(rows) == len({(r["sport"], r["academic_year"], r["term_code"]) for r in rows}))

r = book(start_time="2026-10-05T14:00:00", end_time="2026-10-05T15:00:00",
         venue_id=2, season_id=99999)
check("a client that still sends season_id is ignored, not rejected",
      r.status_code == 201 and r.json()["season_id"] == oct_season, r.text)

print("\n=== Master sheet: nothing configured ===")
os.environ.pop("MASTER_SHEET_BACKEND", None)
check("with no backend set, the sheet is a no-op",
      isinstance(get_master_sheet(), NullMasterSheet))
check("and a no-op sheet is not writable", get_master_sheet().writable is False)

tmp = Path(tempfile.mkdtemp())
csv_path = tmp / "master.csv"

print("\n=== Master sheet: a row in the sheet blocks a venue here ===")
csv_path.write_text(
    "ref,venue,start,end,description,players\n"
    "EXT-1,Main Court,2027-03-10T18:00:00,2027-03-10T20:00:00,Inter-college friendly,\n"
)
sheet = CsvMasterSheet(csv_path)
report = sync_from_sheet(conn, sheet)
conn.commit()
check(f"sync pulled the row in ({report['synced']} synced)", report["synced"] == 1)
check("with no rejected rows and no unmapped venues",
      report["rejected"] == [] and report["unmapped_venues"] == [])

r = book(start_time="2027-03-10T19:00:00", end_time="2027-03-10T20:30:00")
check("a match overlapping the sheet booking at Main Court is refused",
      r.status_code == 409, r.text)
check("and names the sheet row as the reason",
      any(c["type"] == "venue_external" and c["external_ref"] == "EXT-1"
          for c in r.json()["detail"]["conflicts"]), r.text)

r = book(start_time="2027-03-10T19:00:00", end_time="2027-03-10T20:30:00", venue_id=2)
check("the same slot at a different venue is fine", r.status_code == 201, r.text)

print("\n=== Master sheet: a row commits a PLAYER, not just a venue ===")
client.post("/players", headers=BBALL, json={
    "name": "Pashi", "roll_number": "R101", "team_id": 1, "sport": "Basketball"})
csv_path.write_text(
    "ref,venue,start,end,description,players\n"
    "EXT-2,Cricket Ground,2027-03-20T18:00:00,2027-03-20T20:00:00,District trials,R101\n"
)
report = sync_from_sheet(conn, CsvMasterSheet(csv_path))
conn.commit()
check("resync replaced the previous rows rather than stacking up",
      report["synced"] == 1 and report["removed"] == 1)

r = book(start_time="2027-03-20T19:00:00", end_time="2027-03-20T20:30:00", venue_id=1)
check("a match clashing with the sheet's player commitment is refused",
      r.status_code == 409, r.text)
check("and is reported as an external player clash",
      any(c["type"] == "player_external" for c in r.json()["detail"]["conflicts"]), r.text)

r = book(start_time="2027-03-20T21:00:00", end_time="2027-03-20T22:00:00", venue_id=1)
check(f"and the {BLACKOUT_MINUTES}-minute rest rule applies to sheet bookings too",
      r.status_code == 409
      and any(c["type"] == "blackout_external" for c in r.json()["detail"]["conflicts"]),
      r.text)

r = book(start_time="2027-03-20T21:30:00", end_time="2027-03-20T22:30:00", venue_id=1)
check("90 minutes after the sheet booking ends, it is allowed",
      r.status_code == 201, r.text)

print("\n=== Master sheet: withdrawing a row frees the slot ===")
csv_path.write_text("ref,venue,start,end,description,players\n")
report = sync_from_sheet(conn, CsvMasterSheet(csv_path))
conn.commit()
check("the withdrawn row was removed locally", report["removed"] == 1)
r = book(start_time="2027-03-20T18:30:00", end_time="2027-03-20T19:30:00", venue_id=2)
check("and the slot books cleanly now", r.status_code == 201, r.text)

print("\n=== Master sheet: a booking made here can be pushed back ===")
match_id = r.json()["match_id"]
booking = booking_for_match(conn, match_id)
check("the pushed row carries a stable reference",
      booking.external_ref == f"scheduler-match-{match_id}")
check("the venue by name, so a human can read it",
      booking.venue_name == "Cricket Ground", booking.venue_name)
check("and the roll numbers it commits",
      "R101" in booking.roll_numbers, str(booking.roll_numbers))

push_target = tmp / "push.csv"
CsvMasterSheet(push_target).push(booking)
written = push_target.read_text()
check("push writes a header plus the row",
      written.startswith("ref,venue,start,end,description,players")
      and f"scheduler-match-{match_id}" in written, written)

print("\n=== Master sheet: unreadable is never mistaken for empty ===")
try:
    CsvMasterSheet(tmp / "does-not-exist.csv").pull()
    raise AssertionError("a missing sheet must raise, not return []")
except SheetUnavailable:
    check("a missing sheet raises SheetUnavailable rather than returning []", True)

csv_path.write_text(
    "ref,venue,start,end,description,players\n"
    "EXT-BAD,Main Court,not-a-date,also-not-a-date,Broken row,\n"
)
report = sync_from_sheet(conn, CsvMasterSheet(csv_path))
conn.commit()
check("a row with unparseable times is reported, not silently skipped",
      len(report["rejected"]) == 1 and report["rejected"][0]["ref"] == "EXT-BAD",
      str(report))

csv_path.write_text(
    "ref,venue,start,end,description,players\n"
    "EXT-V,Nonexistent Pavilion,2027-03-25T18:00:00,2027-03-25T19:00:00,Unknown venue,\n"
)
report = sync_from_sheet(conn, CsvMasterSheet(csv_path))
conn.commit()
check("a venue name we do not recognise is flagged for a human",
      len(report["unmapped_venues"]) == 1, str(report))

os.environ["MASTER_SHEET_BACKEND"] = "google"
os.environ.pop("MASTER_SHEET_ID", None)
try:
    get_master_sheet()
    raise AssertionError("misconfigured google backend must raise")
except SheetUnavailable:
    check("a half-configured google backend refuses to start quietly", True)
finally:
    os.environ.pop("MASTER_SHEET_BACKEND", None)

conn.close()
print(f"\n\n=== ALL {len(PASSED)} BOOKING-RULE CHECKS PASSED ===")
