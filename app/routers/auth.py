import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.deps import get_db, get_current_user
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    role: str
    sport_scope: str | None


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, conn: sqlite3.Connection = Depends(get_db)):
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (body.email,)
    ).fetchone()
    if user is None or not verify_password(body.password, user["password_hash"]):
        # Deliberately identical error for "no such user" and "wrong
        # password" — don't leak which one it was.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = create_access_token(user["id"], user["role"], user["sport_scope"])
    return LoginResponse(
        access_token=token, role=user["role"], sport_scope=user["sport_scope"]
    )


@router.get("/me")
def me(user: sqlite3.Row = Depends(get_current_user)):
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "sport_scope": user["sport_scope"]}
