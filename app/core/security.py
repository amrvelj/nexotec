"""Password hashing for the interim internal login (issue #8) — bcrypt only,
no other credential material. Kept separate from app.core.auth (JWT/session
boundary) since password storage and session-token issuance are distinct
concerns.
"""

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
