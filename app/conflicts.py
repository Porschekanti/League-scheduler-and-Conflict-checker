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

Two rules about *how* those checks are run, both learned the hard way:

* Times are compared as **instants**, never as raw strings. The columns are
  TEXT, and nothing stops a client sending "2026-10-01 18:30:00" or
  "2026-10-01T23:30:00+05:30" for the very same moment another row stores
  as "2026-10-01T18:00:00". Lexicographic comparison of those silently
  reports "no conflict", which is the worst failure this system can have —
  a missed clash looks exactly like a clean schedule.

* A proposed match that cannot be checked is **rejected**, not waved
  through. An unparseable or backwards interval raises
  MatchValidationError rather than returning an empty conflict list,
  because "I found no conflicts" and "I could not look" must never be the
  same answer.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

# Naive timestamps (no offset) are interpreted in this zone — one fixed
# assumption applied to every naive value, so they stay comparable with
# each other and with offset-aware ones. UTC by default; set
# SCHEDULER_UTC_OFFSET_MINUTES for a campus running on local wall-clock time
# (e.g. 330 for IST).
_NAIVE_OFFSET = timezone(timedelta(minutes=int(os.environ.get("SCHEDULER_UTC_OFFSET_MINUTES", "0"))))


class MatchValidationError(ValueError):
    """The proposed match is malformed, so no meaningful check is possible.

    Distinct from a conflict: a conflict means "this clashes with reality"
    (409), this means "this isn't a well-formed match at all" (422).
    """


def parse_instant(value: str, field: str = "time") -> datetime:
    """Turn a stored/submitted timestamp into a timezone-aware instant."""
    if not isinstance(value, str) or not value.strip():
        raise MatchValidationError(f"{field} is missing")
    text = value.strip().replace(" ", "T", 1)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise MatchValidationError(
            f"{field} '{value}' is not a valid ISO 8601 timestamp"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_NAIVE_OFFSET)
    return parsed.astimezone(timezone.utc)


def normalize_instant(value: str, field: str = "time") -> str:
    """Canonical UTC text for storage, so stored rows sort and compare
    correctly and every row means exactly one unambiguous moment."""
    return parse_instant(value, field).isoformat()


def parse_interval(start_time: str, end_time: str) -> tuple[datetime, datetime]:
    """Parse and sanity-check a match interval.

    A backwards interval is rejected outright: `_overlaps` on a reversed
    range returns False against everything, so accepting one would let a
    caller book on top of any existing match at will.
    """
    start = parse_instant(start_time, "start_time")
    end = parse_instant(end_time, "end_time")
    if end <= start:
        raise MatchValidationError(
            f"end_time ({end_time}) must be strictly after start_time ({start_time})"
        )
    return start, end


def _overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """Half-open [start, end) overlap: back-to-back matches do not clash."""
    return start_a < end_b and start_b < end_a


def _row_interval(row: sqlite3.Row) -> tuple[datetime, datetime] | None:
    """Parse an interval already sitting in the database.

    Rows written before timestamps were normalized may be unparseable. Such
    a row is skipped for overlap maths — but see `find_unparseable_matches`,
    which surfaces them instead of letting them hide.
    """
    try:
        return parse_instant(row["start_time"]), parse_instant(row["end_time"])
    except MatchValidationError:
        return None


def check_venue_conflict(
    conn: sqlite3.Connection,
    venue_id: int,
    start_time: str,
    end_time: str,
    exclude_match_id: int | None = None,
) -> list[dict]:
    start, end = parse_interval(start_time, end_time)

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
        interval = _row_interval(row)
        if interval and _overlaps(start, end, *interval):
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
    """Every commitment of every player on either team, in one query.

    The join deliberately spans all of `team_members`, not just this sport
    or season: a student double-booked across Basketball and Cricket is the
    exact failure this project was built to catch, and narrowing this query
    to the current league would delete that feature.
    """
    start, end = parse_interval(start_time, end_time)

    rows = conn.execute(
        """
        WITH squad AS (
            SELECT DISTINCT tm.player_id AS player_id, p.name AS player_name
            FROM team_members tm
            JOIN players p ON p.id = tm.player_id
            WHERE tm.team_id IN (?, ?)
        ),
        commitments AS (
            SELECT tm.player_id AS player_id, m.id AS match_id,
                   m.start_time AS start_time, m.end_time AS end_time
            FROM team_members tm
            JOIN matches m ON m.home_team_id = tm.team_id
            WHERE m.status != 'CANCELLED'
            UNION
            SELECT tm.player_id, m.id, m.start_time, m.end_time
            FROM team_members tm
            JOIN matches m ON m.away_team_id = tm.team_id
            WHERE m.status != 'CANCELLED'
        )
        SELECT s.player_id, s.player_name, c.match_id, c.start_time, c.end_time
        FROM squad s
        JOIN commitments c ON c.player_id = s.player_id
        WHERE (? IS NULL OR c.match_id != ?)
        ORDER BY s.player_id, c.match_id
        """,
        (home_team_id, away_team_id, exclude_match_id, exclude_match_id),
    ).fetchall()

    conflicts = []
    for row in rows:
        interval = _row_interval(row)
        if interval and _overlaps(start, end, *interval):
            conflicts.append(
                {
                    "type": "player",
                    "player_id": row["player_id"],
                    "player_name": row["player_name"],
                    "conflicting_match_id": row["match_id"],
                }
            )
    return conflicts


def check_match_integrity(
    conn: sqlite3.Connection,
    home_team_id: int,
    away_team_id: int,
    venue_id: int,
    sport: str | None = None,
    season_id: int | None = None,
) -> None:
    """Is this even a real match? Raises MatchValidationError if not.

    Runs before the conflict checks, because a match referencing a team that
    doesn't exist has no roster, so `check_player_conflicts` finds no players
    and cheerfully reports "no conflicts".

    When `sport`/`season_id` are supplied they are checked against the teams
    rather than trusted. They arrive from the request body and are what
    `require_sport_scope` authorized against, so leaving them unverified lets
    a rep labelled for one sport schedule another sport's teams by simply
    claiming their own sport in the payload.
    """
    if home_team_id == away_team_id:
        raise MatchValidationError("home_team_id and away_team_id must differ")

    teams = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id, sport, season_id FROM teams WHERE id IN (?, ?)",
            (home_team_id, away_team_id),
        ).fetchall()
    }
    for label, team_id in (("home_team_id", home_team_id), ("away_team_id", away_team_id)):
        if team_id not in teams:
            raise MatchValidationError(f"{label} {team_id} does not exist")

    if conn.execute("SELECT 1 FROM venues WHERE id = ?", (venue_id,)).fetchone() is None:
        raise MatchValidationError(f"venue_id {venue_id} does not exist")

    home, away = teams[home_team_id], teams[away_team_id]
    if home["sport"] != away["sport"]:
        raise MatchValidationError(
            f"teams are from different sports ({home['sport']} vs {away['sport']})"
        )
    if home["season_id"] != away["season_id"]:
        raise MatchValidationError("teams are from different seasons")
    if sport is not None and home["sport"] != sport:
        raise MatchValidationError(
            f"declared sport '{sport}' does not match the teams' sport '{home['sport']}'"
        )
    if season_id is not None and home["season_id"] != season_id:
        raise MatchValidationError(
            f"declared season_id {season_id} does not match the teams' season "
            f"{home['season_id']}"
        )


def validate_match(
    conn: sqlite3.Connection,
    home_team_id: int,
    away_team_id: int,
    venue_id: int,
    start_time: str,
    end_time: str,
    exclude_match_id: int | None = None,
    sport: str | None = None,
    season_id: int | None = None,
) -> list[dict]:
    """Runs every check and returns a combined, specific conflict list.

    Raises MatchValidationError if the match is malformed — callers must map
    that to a 4xx rather than treating it as "clean".
    """
    check_match_integrity(
        conn, home_team_id, away_team_id, venue_id, sport, season_id
    )
    parse_interval(start_time, end_time)

    conflicts = check_venue_conflict(
        conn, venue_id, start_time, end_time, exclude_match_id
    )
    conflicts += check_player_conflicts(
        conn, home_team_id, away_team_id, start_time, end_time, exclude_match_id
    )
    return conflicts


def find_unparseable_matches(conn: sqlite3.Connection) -> list[dict]:
    """Rows whose stored times cannot be compared, and which therefore sit
    outside conflict detection entirely. Nothing should ever land here now
    that writes are normalized; a non-empty result means legacy rows need
    cleaning up before the schedule can be trusted."""
    bad = []
    for row in conn.execute(
        "SELECT id, start_time, end_time FROM matches WHERE status != 'CANCELLED'"
    ).fetchall():
        if _row_interval(row) is None:
            bad.append(dict(row))
    return bad
