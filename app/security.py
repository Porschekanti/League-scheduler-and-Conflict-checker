import hashlib
import hmac
import os
import time

import jwt

# In a real deployment this comes from an environment variable, never a
# hardcoded literal. Kept simple here since this is a course project.
JWT_SECRET = os.environ.get("SCHEDULER_JWT_SECRET", "dev-secret-change-me")
JWT_ALGO = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 8  # 8 hours


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    salt_hex, digest_hex = stored_hash.split("$")
    salt = bytes.fromhex(salt_hex)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return hmac.compare_digest(candidate.hex(), digest_hex)


def create_access_token(user_id: int, role: str, sport_scope: str | None) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "sport_scope": sport_scope,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
