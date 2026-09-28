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
    # Both follow from the team, which already belongs to exactly one sport and
    # one trimester. Supplying them is allowed but has to agree; omitting them
    # is the normal path now that seasons are not entered by hand.
    sport: str | None = None
    season_id: int | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def add_player(
    body: AddPlayerRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    team = conn.execute("SELECT sport, season_id FROM teams WHERE id = ?", (body.team_id,)).fetchone()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    if (body.sport is not None and body.sport != team["sport"]) or (
        body.season_id is not None and body.season_id != team["season_id"]
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sport and season_id must match the selected team")
    sport = team["sport"]
    season_id = team["season_id"]
    require_sport_scope(user, sport)

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
                sport,
                season_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Player already on a team in {sport} for this season ({exc})",
        )

    return {"player_id": player_id, "team_id": body.team_id}

@router.delete("/{player_id}/teams/{team_id}")
def remove_player_from_team(player_id: int, team_id: int, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD", "REP"))):
    team = conn.execute("SELECT sport, season_id FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team membership not found")
    require_sport_scope(user, team["sport"])
    cur = conn.execute("DELETE FROM team_members WHERE player_id = ? AND team_id = ? AND sport = ? AND season_id = ?", (player_id, team_id, team["sport"], team["season_id"]))
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team membership not found")
    conn.commit()
    return {"removed": True, "player_id": player_id, "team_id": team_id}


