import sqlite3

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.db import get_connection
from app.security import decode_access_token


def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    authorization: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    """Check 1: is this a valid, logged-in user at all?"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (payload["sub"],)
    ).fetchone()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


def require_role(*allowed_roles: str):
    """Check 2: does this role permit this action at all?"""

    def dependency(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{user['role']}' cannot perform this action",
            )
        return user

    return dependency


def require_sport_scope(user: sqlite3.Row, target_sport: str) -> None:
    """Check 3: even if the role allows it, does it apply to THIS sport?

    Sports Head has global scope and is exempt. Sports Rep must match.
    """
    if user["role"] == "HEAD":
        return
    if user["role"] == "REP" and user["sport_scope"] != target_sport:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Rep scoped to '{user['sport_scope']}' cannot act on '{target_sport}'",
        )
