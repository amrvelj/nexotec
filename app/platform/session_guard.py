"""The session guard (FR-AU-17/18, KAN-196): an app-level dependency that
checks every request carrying one of our tokens against its `session` row
before the route runs. A token without `sid` (a test token, bootstrap) is
left to the route; a token whose row is revoked, idle beyond its timeout or
past its absolute end is 401 — the cookie is cleared by the client on 401
(KAN-237), so the user lands on the login screen. Activity extends the idle
end (`auth_sessions.touch`). A malformed or expired token is not judged
here: the route's own `get_current_principal` answers it, so an
unauthenticated route stays reachable with a stale cookie in the jar."""

import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import SESSION_COOKIE_NAME, decode_token_claims
from app.core.base import utcnow
from app.core.errors import UnauthorizedError
from app.db import get_db
from app.platform.services import auth_sessions

_SESSION_ENDED = "Your session has ended — sign in again."


def _token_from(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(SESSION_COOKIE_NAME)


def enforce_live_session(request: Request, db: Session = Depends(get_db)) -> None:
    token = _token_from(request)
    if not token:
        return
    if request.url.path.endswith("/auth/logout"):
        # Signing out of a session that has already ended is the one thing a
        # dead session may still do; the route clears the cookie either way.
        return
    try:
        claims = decode_token_claims(token)
    except UnauthorizedError:
        return  # the route decides; an unauthenticated route must not 401 on a stale cookie
    sid = claims.get("sid")
    if not sid:
        return
    try:
        session_id = uuid.UUID(str(sid))
    except ValueError:
        raise UnauthorizedError(_SESSION_ENDED) from None
    row = auth_sessions.get(db, session_id)
    if row is None or not row.is_live(utcnow()):
        raise UnauthorizedError(_SESSION_ENDED)
    if auth_sessions.touch(db, row):
        db.commit()
