import json
import sqlite3
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.deps import get_db, require_role, require_sport_scope
from app.worker import run_job

router = APIRouter(prefix="/schedule-generations", tags=["jobs"])


class FixtureRequest(BaseModel):
    home_team_id: int
    away_team_id: int
    venue_id: int
    start_time: str
    end_time: str


class GenerationRequest(BaseModel):
    sport: str
    season_id: int
    fixtures: list[FixtureRequest]  # candidate fixtures to attempt


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_generation_job(
    body: GenerationRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    require_sport_scope(user, body.sport)

    payload = body.model_dump()
    cur = conn.execute(
        "INSERT INTO jobs (status, created_at, payload) VALUES ('QUEUED', ?, ?)",
        (datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
    )
    conn.commit()
    job_id = cur.lastrowid

    # The worker claims and completes this independently, off the request
    # path — the API never blocks on it, per the async design.
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()

    return {"job_id": job_id, "status": "QUEUED"}


@router.get("/{job_id}")
def get_job_status(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return {"error": "job not found"}
    result = json.loads(row["result"]) if row["result"] else None
    return {"job_id": job_id, "status": row["status"], "result": result}
