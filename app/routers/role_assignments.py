import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.deps import get_db, get_current_user, require_role

router = APIRouter(tags=["role assignments"])


class TermRequest(BaseModel):
    label: str
    start_date: str
    end_date: str


class NominationRequest(BaseModel):
    role: str
    sport_scope: str | None = None
    term_id: int
    nominee_user_id: int


class AcceptResponse(BaseModel):
    id: int
    status: str
    role: str
    sport_scope: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/terms", status_code=status.HTTP_201_CREATED)
def create_term(body: TermRequest, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD"))):
    try:
        cur = conn.execute("INSERT INTO academic_terms (label, start_date, end_date) VALUES (?, ?, ?)", (body.label, body.start_date, body.end_date))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Term label already exists")
    return {"id": cur.lastrowid, "label": body.label, "start_date": body.start_date, "end_date": body.end_date, "is_current": 0}


@router.post("/terms/{term_id}/activate")
def activate_term(term_id: int, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD"))):
    if conn.execute("SELECT id FROM academic_terms WHERE id = ?", (term_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Term not found")
    conn.execute("UPDATE academic_terms SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END", (term_id,))
    conn.commit()
    return {"term_id": term_id, "is_current": True}


@router.get("/terms")
def list_terms(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(r) for r in conn.execute("SELECT id, label, start_date, end_date, is_current FROM academic_terms ORDER BY start_date DESC, id DESC").fetchall()]


@router.post("/role-assignments/nominate", status_code=status.HTTP_201_CREATED)
def nominate(body: NominationRequest, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD"))):
    if body.role not in ("HEAD", "REP"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "role must be HEAD or REP")
    if body.role == "REP" and not body.sport_scope:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "REP nominations require sport_scope")
    if body.role == "HEAD" and body.sport_scope is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "HEAD nominations must not include sport_scope")
    if conn.execute("SELECT id FROM academic_terms WHERE id = ?", (body.term_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Term not found")
    if conn.execute("SELECT id FROM users WHERE id = ?", (body.nominee_user_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nominee not found")
    cur = conn.execute("""INSERT INTO role_assignments
        (role, sport_scope, term_id, nominated_user_id, nominated_by_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""", (body.role, body.sport_scope, body.term_id, body.nominee_user_id, user["id"], _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM role_assignments WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/role-assignments/pending-for-me")
def pending_for_me(conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(get_current_user)):
    rows = conn.execute("SELECT * FROM role_assignments WHERE nominated_user_id = ? AND status = 'PENDING' ORDER BY created_at DESC", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@router.post("/role-assignments/{assignment_id}/accept", response_model=AcceptResponse)
def accept_assignment(assignment_id: int, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(get_current_user)):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM role_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if row is None:
        conn.rollback(); raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    if row["nominated_user_id"] != user["id"]:
        conn.rollback(); raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the nominee can accept this assignment")
    if row["status"] != "PENDING":
        conn.rollback(); raise HTTPException(status.HTTP_409_CONFLICT, "Assignment is no longer pending")
    conn.execute("UPDATE role_assignments SET status = 'ACCEPTED', accepted_at = ? WHERE id = ?", (_now(), assignment_id))
    conn.execute("UPDATE users SET role = ?, sport_scope = ? WHERE id = ?", (row["role"], row["sport_scope"], user["id"]))
    conn.commit()
    return {"id": assignment_id, "status": "ACCEPTED", "role": row["role"], "sport_scope": row["sport_scope"]}


@router.post("/role-assignments/{assignment_id}/revoke")
def revoke_assignment(assignment_id: int, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD"))):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM role_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if row is None:
        conn.rollback(); raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    if row["status"] != "ACCEPTED":
        conn.rollback(); raise HTTPException(status.HTTP_409_CONFLICT, "Only accepted assignments can be revoked")
    conn.execute("UPDATE role_assignments SET status = 'REVOKED' WHERE id = ?", (assignment_id,))
    conn.execute("UPDATE users SET role = 'VIEWER', sport_scope = NULL WHERE id = ?", (row["nominated_user_id"],))
    previous = conn.execute("""SELECT nominated_user_id FROM role_assignments
        WHERE role = ? AND (sport_scope IS ? OR sport_scope = ?) AND status = 'ACCEPTED' AND id != ?
        ORDER BY accepted_at DESC, id DESC LIMIT 1""", (row["role"], row["sport_scope"], row["sport_scope"], assignment_id)).fetchone()
    if previous:
        conn.execute("UPDATE users SET role = ?, sport_scope = ? WHERE id = ?", (row["role"], row["sport_scope"], previous["nominated_user_id"]))
        fallback = previous["nominated_user_id"]
    else:
        fallback = None
    conn.commit()
    return {"id": assignment_id, "status": "REVOKED", "fallback_user_id": fallback}


@router.get("/role-assignments")
def assignment_history(role: str | None = Query(default=None), sport_scope: str | None = Query(default=None), conn: sqlite3.Connection = Depends(get_db)):
    sql = "SELECT * FROM role_assignments WHERE 1=1"; params = []
    if role is not None: sql += " AND role = ?"; params.append(role)
    if sport_scope is not None: sql += " AND sport_scope = ?"; params.append(sport_scope)
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC, id DESC", params).fetchall()]


@router.get("/users/lookup")
def lookup_user(email: str, conn: sqlite3.Connection = Depends(get_db), user: sqlite3.Row = Depends(require_role("HEAD"))):
    row = conn.execute("SELECT id, name, email, role, sport_scope FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return dict(row)
