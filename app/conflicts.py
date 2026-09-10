"""
The two independent checks the whole system exists to run, before any
match is confirmed:

1. Venue conflict  — does this venue/time overlap an existing booking?
2. Player conflict — is any player on either team already committed to
   an overlapping match, in ANY sport/season (not just this one)?

Both checks query live data at call time — there is no cached or stale
copy of "the schedule" anywhere. That is the actual fix for the
one-directional, asynchronous problem described in the design doc: every
proposed match is checked against everything that exists *right now*.
"""
import sqlite3


def _overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    return start_a < end_b and start_b < end_a


def check_venue_conflict(
    conn: sqlite3.Connection,
    venue_id: int,
    start_time: str,
    end_time: str,
    exclude_match_id: int | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, start_time, end_time FROM matches
        WHERE venue_id = ? AND status != 'CANCELLED'
          AND (? IS NULL OR id != ?)
        """,
        (venue_id, exclude_match_id, exclude_match_id),
    ).fetchall()

    conflicts = []
    for row in rows:
        if _overlaps(start_time, end_time, row["start_time"], row["end_time"]):
            conflicts.append({"type": "venue", "conflicting_match_id": row["id"]})
    return conflicts


def check_player_conflicts(
    conn: sqlite3.Connection,
    home_team_id: int,
    away_team_id: int,
    start_time: str,
    end_time: str,
    exclude_match_id: int | None = None,
) -> list[dict]:
    players = conn.execute(
        """
        SELECT DISTINCT tm.player_id, p.name FROM team_members tm
        JOIN players p ON p.id = tm.player_id
        WHERE tm.team_id IN (?, ?)
        """,
        (home_team_id, away_team_id),
    ).fetchall()

    conflicts = []
    for player in players:
        other_matches = conn.execute(
            """
            SELECT DISTINCT m.id, m.start_time, m.end_time FROM matches m
            JOIN team_members tm ON tm.team_id IN (m.home_team_id, m.away_team_id)
            WHERE tm.player_id = ? AND m.status != 'CANCELLED'
              AND (? IS NULL OR m.id != ?)
            """,
            (player["player_id"], exclude_match_id, exclude_match_id),
        ).fetchall()
        for m in other_matches:
            if _overlaps(start_time, end_time, m["start_time"], m["end_time"]):
                conflicts.append(
                    {
                        "type": "player",
                        "player_id": player["player_id"],
                        "player_name": player["name"],
                        "conflicting_match_id": m["id"],
                    }
                )
    return conflicts


def validate_match(
    conn: sqlite3.Connection,
    home_team_id: int,
    away_team_id: int,
    venue_id: int,
    start_time: str,
    end_time: str,
    exclude_match_id: int | None = None,
) -> list[dict]:
    """Runs both checks and returns a combined, specific conflict list."""
    conflicts = check_venue_conflict(
        conn, venue_id, start_time, end_time, exclude_match_id
    )
    conflicts += check_player_conflicts(
        conn, home_team_id, away_team_id, start_time, end_time, exclude_match_id
    )
    return conflicts
