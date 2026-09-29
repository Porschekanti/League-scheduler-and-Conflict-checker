"""Create a self-contained database for the final presentation.

This keeps the normal development database untouched and produces
``presentation.db`` (or the path supplied with ``--database``).  It starts
with the normal seeded staff, teams and venues, then adds representative
rosters and confirmed matches so the public schedule and conflict checks have
useful data immediately.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("presentation.db"))
    args = parser.parse_args()
    if args.database.exists():
        args.database.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{args.database}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    env = os.environ.copy()
    env["SCHEDULER_DB_PATH"] = str(args.database)
    subprocess.run([sys.executable, "seed.py"], check=True, env=env)

    os.environ["SCHEDULER_DB_PATH"] = str(args.database)
    from app.conflicts import normalize_instant
    from app.db import get_connection, set_actor

    conn = get_connection()
    set_actor(conn, "SYSTEM")
    try:
        basketball = conn.execute(
            "SELECT id FROM seasons WHERE sport = 'Basketball' ORDER BY id LIMIT 1"
        ).fetchone()[0]
        cricket = conn.execute(
            "SELECT id FROM seasons WHERE sport = 'Cricket' ORDER BY id LIMIT 1"
        ).fetchone()[0]
        roster = [
            ("Aarav Sharma", "202401", 1, "Basketball", basketball),
            ("Riya Shah", "202417", 1, "Basketball", basketball),
            ("Kabir Mehta", "202423", 2, "Basketball", basketball),
            ("Neha Singh", "202431", 3, "Cricket", cricket),
            ("Ishaan Rao", "202438", 4, "Cricket", cricket),
        ]
        for name, roll, team_id, sport, season_id in roster:
            player = conn.execute(
                "INSERT INTO players (name, roll_number, status) VALUES (?, ?, 'ACTIVE')",
                (name, roll),
            )
            conn.execute(
                "INSERT INTO team_members (team_id, player_id, sport, season_id, joined_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (team_id, player.lastrowid, sport, season_id),
            )

        matches = [
            (1, 2, 1, "2026-10-08T18:00:00+00:00", "2026-10-08T19:00:00+00:00", "Basketball", basketball),
            (3, 4, 2, "2026-10-09T18:00:00+00:00", "2026-10-09T20:00:00+00:00", "Cricket", cricket),
        ]
        for home, away, venue, start, end, sport, season_id in matches:
            conn.execute(
                "INSERT INTO matches (home_team_id, away_team_id, venue_id, start_time, end_time, "
                "status, sport, season_id, version) VALUES (?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, 1)",
                (home, away, venue, normalize_instant(start), normalize_instant(end), sport, season_id),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"Presentation database ready: {args.database.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
