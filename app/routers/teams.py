import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from app.deps import get_db, require_role, require_sport_scope

router = APIRouter(tags=["reference"])

class CreateTeamRequest(BaseModel):
    name: str
    sport: str
    season_id: int

@router.post("/teams", status_code=status.HTTP_201_CREATED)
def create_team(body: CreateTeamRequest, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD", "REP"))):
    require_sport_scope(user, body.sport)
    season = conn.execute("SELECT sport FROM seasons WHERE id = ?", (body.season_id,)).fetchone()
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Season not found")
    if season["sport"] != body.sport:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sport must match the season")
    cur = conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, ?, ?)", (body.name, body.sport, body.season_id))
    conn.commit()
    return {"team_id": cur.lastrowid}

@router.get("/teams")
def list_teams(sport: str | None = Query(default=None), season_id: int | None = Query(default=None), conn: sqlite3.Connection = Depends(get_db)):
    sql = "SELECT id, name, sport, season_id FROM teams WHERE 1=1"; params = []
    if sport is not None: sql += " AND sport = ?"; params.append(sport)
    if season_id is not None: sql += " AND season_id = ?"; params.append(season_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY name", params).fetchall()]

@router.get("/venues")
def list_venues(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(r) for r in conn.execute("SELECT id, name, location, capacity FROM venues ORDER BY name").fetchall()]

@router.get("/seasons")
def list_seasons(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(r) for r in conn.execute("SELECT id, sport, start_date, end_date, is_active FROM seasons ORDER BY start_date DESC, id").fetchall()]

@router.get("/teams/{team_id}/roster")
def team_roster(team_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if conn.execute("SELECT id FROM teams WHERE id = ?", (team_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    rows = conn.execute("""SELECT p.id, p.name, p.roll_number, p.status, tm.sport, tm.season_id, tm.joined_at
                          FROM team_members tm JOIN players p ON p.id = tm.player_id
                          WHERE tm.team_id = ? ORDER BY p.name""", (team_id,)).fetchall()
    return [dict(r) for r in rows]
