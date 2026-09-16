import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.conflicts import MatchValidationError, normalize_instant, validate_match
from app.deps import get_db, require_role, require_sport_scope

router = APIRouter(prefix="/schedules", tags=["schedules"])


class DraftMatchRequest(BaseModel):
    home_team_id: int
    away_team_id: int
    venue_id: int
    start_time: str  # ISO 8601
    end_time: str
    sport: str
    season_id: int


@router.post("", status_code=status.HTTP_201_CREATED)
def create_draft(
    body: DraftMatchRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    """Booking Service's direct path: propose one specific match."""
    require_sport_scope(user, body.sport)

    try:
        start_time = normalize_instant(body.start_time, "start_time")
        end_time = normalize_instant(body.end_time, "end_time")

        # Checking and then inserting is only safe inside one transaction.
        # Handlers are sync `def`, so FastAPI runs them in a threadpool and
        # concurrent bookings really do interleave — without BEGIN IMMEDIATE
        # two requests both see a free slot and both insert into it.
        conn.execute("BEGIN IMMEDIATE")

        conflicts = validate_match(
            conn,
            body.home_team_id,
            body.away_team_id,
            body.venue_id,
            start_time,
            end_time,
            sport=body.sport,
            season_id=body.season_id,
        )
        if conflicts:
            conn.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, {"conflicts": conflicts})

        cur = conn.execute(
            """
            INSERT INTO matches
                (home_team_id, away_team_id, venue_id, start_time, end_time,
                 status, sport, season_id, version)
            VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, 1)
            """,
            (
                body.home_team_id,
                body.away_team_id,
                body.venue_id,
                start_time,
                end_time,
                body.sport,
                body.season_id,
            ),
        )
        conn.commit()
    except MatchValidationError as exc:
        conn.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise

    return {"match_id": cur.lastrowid, "status": "DRAFT", "version": 1}


@router.patch("/{draft_id}")
def validate_draft(
    draft_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    """Re-check a draft against everything that exists right now, without
    committing anything. Lets an organizer see if their tweak is still
    conflict-free before attempting to publish."""
    match = conn.execute("SELECT * FROM matches WHERE id = ?", (draft_id,)).fetchone()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    require_sport_scope(user, match["sport"])

    try:
        conflicts = validate_match(
            conn,
            match["home_team_id"],
            match["away_team_id"],
            match["venue_id"],
            match["start_time"],
            match["end_time"],
            exclude_match_id=draft_id,
        )
    except MatchValidationError as exc:
        # A stored row we cannot even parse is never "valid" — reporting it
        # as conflict-free would hide it from the organizer entirely.
        return {"valid": False, "conflicts": [{"type": "invalid", "reason": str(exc)}]}

    if conflicts:
        return {"valid": False, "conflicts": conflicts}
    return {"valid": True, "conflicts": []}


class PublishRequest(BaseModel):
    expected_version: int


@router.post("/{draft_id}/publish")
def publish_draft(
    draft_id: int,
    body: PublishRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    """BEGIN transaction + revalidate + version check, exactly as in the
    revised design doc. No Redis lock: a conflicting concurrent publish is
    caught by the version check and rejected with 409, not silently
    overwritten."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        match = conn.execute(
            "SELECT * FROM matches WHERE id = ?", (draft_id,)
        ).fetchone()
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
        require_sport_scope(user, match["sport"])

        if match["status"] != "DRAFT":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Match is not in DRAFT status"
            )

        if match["version"] != body.expected_version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Version mismatch — someone else already changed this draft "
                f"(expected {body.expected_version}, current {match['version']})",
            )

        try:
            conflicts = validate_match(
                conn,
                match["home_team_id"],
                match["away_team_id"],
                match["venue_id"],
                match["start_time"],
                match["end_time"],
                exclude_match_id=draft_id,
            )
        except MatchValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Draft cannot be published: {exc}",
            )

        if conflicts:
            raise HTTPException(
                status.HTTP_409_CONFLICT, {"conflicts": conflicts}
            )

        conn.execute(
            """
            UPDATE matches SET status = 'CONFIRMED', version = version + 1
            WHERE id = ? AND version = ?
            """,
            (draft_id, body.expected_version),
        )
        conn.commit()
    except Exception:
        # Every early exit above leaves BEGIN IMMEDIATE's write lock held
        # otherwise, which blocks other writers until the connection closes.
        conn.rollback()
        raise

    return {
        "match_id": draft_id,
        "status": "CONFIRMED",
        "version": body.expected_version + 1,
    }


@router.get("")
def list_schedules(season: str = "active", conn: sqlite3.Connection = Depends(get_db)):
    """Viewer read path: CONFIRMED matches only, no auth required."""
    if season == "active":
        rows = conn.execute(
            """
            SELECT m.* FROM matches m
            JOIN seasons s ON s.id = m.season_id
            WHERE m.status = 'CONFIRMED' AND s.is_active = 1
            ORDER BY m.start_time
            """
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM matches WHERE status = 'CONFIRMED' ORDER BY start_time"
        ).fetchall()
    return [dict(r) for r in rows]
