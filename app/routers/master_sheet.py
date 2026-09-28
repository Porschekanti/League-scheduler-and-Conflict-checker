import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_db, require_role
from app.sheets import SheetUnavailable, get_master_sheet, sync_from_sheet

router = APIRouter(prefix="/master-sheet", tags=["master sheet"])


@router.get("/status")
def sheet_status(
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD", "REP")),
):
    """What the system currently believes is in the sheet.

    Reports the mirrored rows, not the sheet itself — that is the point of the
    mirror. If these numbers look stale, run a sync.
    """
    try:
        sheet = get_master_sheet()
        configured, detail = True, sheet.name
    except SheetUnavailable as exc:
        configured, detail = False, str(exc)

    rows = conn.execute(
        """
        SELECT source, COUNT(*) AS bookings, MAX(synced_at) AS last_synced
        FROM external_bookings GROUP BY source
        """
    ).fetchall()
    return {
        "configured": configured,
        "backend": detail,
        "mirrored": [dict(r) for r in rows],
    }


@router.post("/sync")
def sync(
    conn: sqlite3.Connection = Depends(get_db),
    user: sqlite3.Row = Depends(require_role("HEAD")),
):
    """Pull the sheet into the local mirror the conflict engine reads.

    HEAD only: a sync changes what every sport is allowed to book, so it is not
    a per-sport action. Rows that could not be read come back under `rejected`
    rather than being dropped — a booking the engine cannot see is a booking it
    cannot protect against.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        report = sync_from_sheet(conn)
        conn.commit()
    except SheetUnavailable as exc:
        conn.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except Exception:
        conn.rollback()
        raise
    return report
