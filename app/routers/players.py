import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.deps import get_db, require_role, require_sport_scope

router = APIRouter(prefix="/players", tags=["players"])


class AddPlayerRequest(BaseModel):
    name: str
    roll_number: str
    team_id: int
    sport: str
    season_id: int


@router.post("", status_code=status.HTTP_201_CREATED)
def add_player(
    body: AddPlayerRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    require_sport_scope(user, body.sport)

    try:
        conn.execute("BEGIN IMMEDIATE")

        # A player might already exist (playing a different sport) — reuse
        # them by roll_number rather than creating a duplicate person.
        existing = conn.execute(
            "SELECT id FROM players WHERE roll_number = ?", (body.roll_number,)
        ).fetchone()
        if existing:
            player_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO players (name, roll_number, status) VALUES (?, ?, 'ACTIVE')",
                (body.name, body.roll_number),
            )
            player_id = cur.lastrowid

        # This insert is where the real duplication rule lives: the
        # UNIQUE(player_id, sport, season_id) constraint on team_members
        # rejects a second team in the same sport+season atomically.
        conn.execute(
            """
            INSERT INTO team_members (team_id, player_id, sport, season_id, joined_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                body.team_id,
                player_id,
                body.sport,
                body.season_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Player already on a team in {body.sport} for this season ({exc})",
        )

    return {"player_id": player_id, "team_id": body.team_id}
