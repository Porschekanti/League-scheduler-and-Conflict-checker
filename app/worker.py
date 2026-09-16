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

The fixture loop is one transaction, so a job that dies partway through
leaves nothing behind. Half-written drafts would be worse than no drafts:
they are invisible in the job result yet still block the venue and the
players from being scheduled by anyone else.
"""
import json
import time

from app.conflicts import MatchValidationError, normalize_instant, validate_match
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
        # actually observable rather than completing instantly. Deliberately
        # outside the transaction below — holding a write lock through this
        # would stall every other writer for its duration.
        time.sleep(1.5)

        created, skipped = [], []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for fixture in payload["fixtures"]:
                try:
                    start_time = normalize_instant(fixture["start_time"], "start_time")
                    end_time = normalize_instant(fixture["end_time"], "end_time")
                    conflicts = validate_match(
                        conn,
                        fixture["home_team_id"],
                        fixture["away_team_id"],
                        fixture["venue_id"],
                        start_time,
                        end_time,
                        sport=sport,
                        season_id=season_id,
                    )
                except MatchValidationError as exc:
                    # One malformed candidate must not sink the whole batch —
                    # report it the same way a conflict is reported.
                    skipped.append(
                        {
                            "fixture": fixture,
                            "conflicts": [{"type": "invalid", "reason": str(exc)}],
                        }
                    )
                    continue

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
                        start_time,
                        end_time,
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
        except Exception:
            # Drops every draft this job inserted, so the reported result and
            # the database always agree.
            conn.rollback()
            raise
    except Exception as exc:  # noqa: BLE001 - worker boundary, must not raise
        conn.rollback()
        conn.execute(
            "UPDATE jobs SET status = 'FAILED', result = ? WHERE id = ?",
            (json.dumps({"error": str(exc)}), job_id),
        )
        conn.commit()
    finally:
        conn.close()
