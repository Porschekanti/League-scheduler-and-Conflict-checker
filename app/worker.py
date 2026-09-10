"""
The Worker/Solver process from the architecture diagram. It runs
independently of the request that created the job — claims a QUEUED job,
does the (potentially slow) work, and writes the result back.

This implements the actual generate -> filter design from earlier in the
project, rather than a full constraint-optimization solver: for each
candidate fixture the caller proposed, run the same venue + player
conflict checks used everywhere else, keep the ones that are clear as new
DRAFT matches, and report the ones that were skipped along with exactly
why. It reuses conflicts.validate_match rather than reimplementing any
conflict logic — the same rule set applies whether a match is booked
directly or generated here.
"""
import json
import time

from app.conflicts import validate_match
from app.db import get_connection


def run_job(job_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET status = 'RUNNING' WHERE id = ?", (job_id,)
        )
        conn.commit()

        row = conn.execute(
            "SELECT payload FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        payload = json.loads(row["payload"])
        sport = payload["sport"]
        season_id = payload["season_id"]

        # Simulate non-trivial solver work so the async/poll behavior is
        # actually observable rather than completing instantly.
        time.sleep(1.5)

        created, skipped = [], []
        for fixture in payload["fixtures"]:
            conflicts = validate_match(
                conn,
                fixture["home_team_id"],
                fixture["away_team_id"],
                fixture["venue_id"],
                fixture["start_time"],
                fixture["end_time"],
            )
            if conflicts:
                skipped.append({"fixture": fixture, "conflicts": conflicts})
                continue

            cur = conn.execute(
                """
                INSERT INTO matches
                    (home_team_id, away_team_id, venue_id, start_time, end_time,
                     status, sport, season_id, version, job_id)
                VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, 1, ?)
                """,
                (
                    fixture["home_team_id"],
                    fixture["away_team_id"],
                    fixture["venue_id"],
                    fixture["start_time"],
                    fixture["end_time"],
                    sport,
                    season_id,
                    job_id,
                ),
            )
            created.append(cur.lastrowid)

        result = {"created_draft_match_ids": created, "skipped": skipped}
        conn.execute(
            "UPDATE jobs SET status = 'COMPLETED', result = ? WHERE id = ?",
            (json.dumps(result), job_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - worker boundary, must not raise
        conn.execute(
            "UPDATE jobs SET status = 'FAILED', result = ? WHERE id = ?",
            (json.dumps({"error": str(exc)}), job_id),
        )
        conn.commit()
    finally:
        conn.close()
