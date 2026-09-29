"""Create a deterministic, self-contained database for the MVP demo.

The normal ``seed.py`` is intentionally small and useful for development. This
seed is the richer presentation dataset: it gives the public schedule useful
content immediately, puts players in both sports so cross-league conflicts can
be demonstrated, leaves a pending role nomination for succession, and mirrors
the CSV master sheet so external bookings participate in validation.

Usage::

    python presentation_seed.py
    python presentation_seed.py --database presentation.db

The database is deliberately dated in the 2026-27 academic year so the demo is
repeatable regardless of the day on which it is prepared.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_SHEET = ROOT / "fixtures" / "master_bookings.csv"


def _insert_user(conn: sqlite3.Connection, name: str, email: str, password: str,
                 role: str, sport_scope: str | None = None) -> int:
    from app.security import hash_password

    cur = conn.execute(
        """INSERT INTO users (name, email, password_hash, role, sport_scope)
           VALUES (?, ?, ?, ?, ?)""",
        (name, email, hash_password(password), role, sport_scope),
    )
    return int(cur.lastrowid)


def _insert_season(conn: sqlite3.Connection, sport: str) -> int:
    cur = conn.execute(
        """INSERT INTO seasons
           (sport, start_date, end_date, is_active, academic_year, term_code)
           VALUES (?, '2026-10-01', '2026-12-24', 1, 2026, 'T2')""",
        (sport,),
    )
    return int(cur.lastrowid)


def _insert_team(conn: sqlite3.Connection, name: str, sport: str, season_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO teams (name, sport, season_id) VALUES (?, ?, ?)",
        (name, sport, season_id),
    )
    return int(cur.lastrowid)


def _insert_player(conn: sqlite3.Connection, name: str, roll: str) -> int:
    cur = conn.execute(
        "INSERT INTO players (name, roll_number, status) VALUES (?, ?, 'ACTIVE')",
        (name, roll),
    )
    return int(cur.lastrowid)


def _join(conn: sqlite3.Connection, team_id: int, player_id: int,
          sport: str, season_id: int) -> None:
    conn.execute(
        """INSERT INTO team_members
           (team_id, player_id, sport, season_id, joined_at)
           VALUES (?, ?, ?, ?, ?)""",
        (team_id, player_id, sport, season_id, "2026-09-01T09:00:00+00:00"),
    )


def _insert_match(conn: sqlite3.Connection, home: int, away: int, venue: int,
                  start: str, end: str, sport: str, season_id: int,
                  status: str = "CONFIRMED", version: int = 2) -> int:
    cur = conn.execute(
        """INSERT INTO matches
           (home_team_id, away_team_id, venue_id, start_time, end_time,
            status, sport, season_id, version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (home, away, venue, start, end, status, sport, season_id, version),
    )
    return int(cur.lastrowid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "presentation.db")
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    args = parser.parse_args()

    # The target is an explicitly named demo database, not the source tree.
    # Rebuilding it makes the walkthrough repeatable.
    target = args.database.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{target}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    os.environ["SCHEDULER_DB_PATH"] = str(target)
    os.environ["MASTER_SHEET_BACKEND"] = "csv"
    os.environ["MASTER_SHEET_CSV"] = str(args.sheet.resolve())

    from app.db import get_connection, init_db, set_actor

    init_db()
    conn = get_connection()
    set_actor(conn, "SYSTEM")
    try:
        # Staff identities. demo-nominee starts as VIEWER so its pending
        # acceptance card is visible when that account signs in.
        head_id = _insert_user(
            conn, "College Sports Head", "head@example.edu", "head-pass", "HEAD"
        )
        bball_rep_id = _insert_user(
            conn, "Basketball Rep", "bball-rep@example.edu", "rep-pass",
            "REP", "Basketball"
        )
        cricket_rep_id = _insert_user(
            conn, "Cricket Rep", "cricket-rep@example.edu", "rep-pass",
            "REP", "Cricket"
        )
        nominee_id = _insert_user(
            conn, "Demo Nominee", "nominee@example.edu", "nominee-pass", "VIEWER"
        )

        conn.execute(
            """INSERT INTO academic_terms
               (label, start_date, end_date, is_current)
               VALUES ('2025-26', '2025-07-01', '2026-06-30', 0)"""
        )
        current_term = conn.execute(
            """INSERT INTO academic_terms
               (label, start_date, end_date, is_current)
               VALUES ('2026-27', '2026-07-01', '2027-06-30', 1)"""
        ).lastrowid

        # Accepted rows make Admin > Assignment history meaningful. The
        # pending row powers the nominee's accept path.
        conn.execute(
            """INSERT INTO role_assignments
               (role, sport_scope, term_id, nominated_user_id,
                nominated_by_user_id, status, created_at, accepted_at)
               VALUES ('HEAD', NULL, ?, ?, ?, 'ACCEPTED', ?, ?)""",
            (current_term, head_id, head_id,
             "2026-07-01T09:00:00+00:00", "2026-07-01T09:00:00+00:00"),
        )
        for role, scope, user_id, stamp in (
            ("REP", "Basketball", bball_rep_id, "2026-07-02T09:00:00+00:00"),
            ("REP", "Cricket", cricket_rep_id, "2026-07-03T09:00:00+00:00"),
        ):
            conn.execute(
                """INSERT INTO role_assignments
                   (role, sport_scope, term_id, nominated_user_id,
                    nominated_by_user_id, status, created_at, accepted_at)
                   VALUES (?, ?, ?, ?, ?, 'ACCEPTED', ?, ?)""",
                (role, scope, current_term, user_id, head_id, stamp, stamp),
            )
        pending_assignment = conn.execute(
            """INSERT INTO role_assignments
               (role, sport_scope, term_id, nominated_user_id,
                nominated_by_user_id, status, created_at)
               VALUES ('REP', 'Basketball', ?, ?, ?, 'PENDING', ?)""",
            (current_term, nominee_id, head_id, "2026-09-20T09:00:00+00:00"),
        ).lastrowid

        basketball = _insert_season(conn, "Basketball")
        cricket = _insert_season(conn, "Cricket")

        venues = {}
        for name, location, capacity in (
            ("Main Court", "Sports Complex", 200),
            ("Cricket Ground", "East Campus", 500),
            ("Indoor Hall", "North Block", 120),
        ):
            cur = conn.execute(
                "INSERT INTO venues (name, location, capacity) VALUES (?, ?, ?)",
                (name, location, capacity),
            )
            venues[name] = int(cur.lastrowid)

        teams = {
            "Goon Squad": _insert_team(conn, "Goon Squad", "Basketball", basketball),
            "Rebels": _insert_team(conn, "Rebels", "Basketball", basketball),
            "Strikers": _insert_team(conn, "Strikers", "Cricket", cricket),
            "Chargers": _insert_team(conn, "Chargers", "Cricket", cricket),
            "Falcons": _insert_team(conn, "Falcons", "Basketball", basketball),
            "Comets": _insert_team(conn, "Comets", "Cricket", cricket),
        }

        players = {}
        for name, roll in (
            ("Aarav Sharma", "202401"),
            ("Riya Shah", "202417"),
            ("Kabir Mehta", "202423"),
            ("Ishaan Rao", "202438"),
            ("Meera Iyer", "202442"),
            ("Neha Singh", "202431"),
            ("Tara Kapoor", "202455"),
            ("Vihaan Das", "202462"),
            ("Zoya Khan", "202470"),
        ):
            players[roll] = _insert_player(conn, name, roll)

        # Aarav and Neha deliberately play in both sports. The conflict engine
        # will therefore demonstrate cross-league player conflicts.
        for roll in ("202401", "202417", "202423"):
            _join(conn, teams["Goon Squad"], players[roll], "Basketball", basketball)
        for roll in ("202438", "202442"):
            _join(conn, teams["Rebels"], players[roll], "Basketball", basketball)
        for roll in ("202431", "202455", "202462"):
            _join(conn, teams["Falcons"], players[roll], "Basketball", basketball)
        for roll in ("202401", "202431", "202470"):
            _join(conn, teams["Strikers"], players[roll], "Cricket", cricket)
        for roll in ("202455", "202462", "202442"):
            _join(conn, teams["Chargers"], players[roll], "Cricket", cricket)
        for roll in ("202417", "202423", "202438"):
            _join(conn, teams["Comets"], players[roll], "Cricket", cricket)

        _insert_match(
            conn, teams["Goon Squad"], teams["Rebels"], venues["Main Court"],
            "2026-10-08T18:00:00+00:00", "2026-10-08T19:00:00+00:00",
            "Basketball", basketball,
        )
        _insert_match(
            conn, teams["Chargers"], teams["Comets"], venues["Cricket Ground"],
            "2026-10-09T18:00:00+00:00", "2026-10-09T20:00:00+00:00",
            "Cricket", cricket,
        )
        _insert_match(
            conn, teams["Rebels"], teams["Falcons"], venues["Indoor Hall"],
            "2026-10-11T18:00:00+00:00", "2026-10-11T19:00:00+00:00",
            "Basketball", basketball,
        )
        # A cancelled row proves that cancelled bookings disappear from the
        # public schedule and stop blocking the venue.
        _insert_match(
            conn, teams["Falcons"], teams["Goon Squad"], venues["Main Court"],
            "2026-10-12T18:00:00+00:00", "2026-10-12T19:00:00+00:00",
            "Basketball", basketball, status="CANCELLED", version=2,
        )
        # A known draft gives API presenters a ready row for PATCH validation,
        # publish, optimistic-version and cancel demonstrations.
        draft_id = _insert_match(
            conn, teams["Falcons"], teams["Goon Squad"], venues["Indoor Hall"],
            "2026-10-13T18:00:00+00:00", "2026-10-13T19:00:00+00:00",
            "Basketball", basketball, status="DRAFT", version=1,
        )

        # Mirror the CSV master sheet into the same local tables used by the
        # conflict engine. This keeps the database useful even if the app is
        # started later with the sheet backend temporarily set to ``none``.
        from app.sheets import get_master_sheet, sync_from_sheet
        sync_report = sync_from_sheet(conn, get_master_sheet())
        conn.commit()
    finally:
        conn.close()

    print(f"Presentation database ready: {target}")
    print("Accounts:")
    print("  head@example.edu / head-pass")
    print("  bball-rep@example.edu / rep-pass")
    print("  cricket-rep@example.edu / rep-pass")
    print("  nominee@example.edu / nominee-pass  (pending Basketball REP)")
    print(f"Known draft match id: {draft_id} (version 1)")
    print(f"Pending assignment id: {pending_assignment}")
    print(f"Master sheet: {sync_report['synced']} rows mirrored from {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
