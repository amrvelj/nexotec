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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SESSION_COOKIE_NAME, AccessRole, decode_token_claims
from app.core.base import utcnow
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db import get_db
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.services import auth_events, auth_sessions, mfa

_SESSION_ENDED = "Your session has ended — sign in again."
_ENROL_FIRST = "Enrol a second factor before anything else — your Betrieb requires one."
# What a session minted without a required second factor may still reach
# (D-A-36, FR-AU-11): its own identity, the enrolment itself, its sessions,
# the UI preferences the shell loads, and the way out.
# Everything under /v1/auth (the front door is unauthenticated anyway; the
# enrolment, the sessions, logout) and the preferences the shell loads.
_ENROLMENT_PATHS = ("/v1/auth/", "/v1/me/preferences")


def _enrolment_path(path: str, claims: dict) -> bool:
    if not path.startswith("/v1/"):
        return True  # health, the SPA's own files — not the API's business
    if path.startswith(_ENROLMENT_PATHS):
        return True
    # The Betrieb's OWN enforcement switch stays reachable to its manager: a
    # manager who decides against a second factor turns it off instead of
    # enrolling. Never for a staff session (enforced whatever the switch
    # says) and never another Betrieb's.
    own_switch = f"/v1/dealerships/{claims.get('tenant_id')}/auth-config"
    return path == own_switch and AccessRole.PLATFORM_ADMIN.value not in (claims.get("roles") or ())


def _enrolment_owed(db: Session, claims: dict, row: AuthSession) -> bool:
    """A session with no second factor while one is required: every platform
    staff account (D-A-36); a dealer user when the Betrieb enforces it
    (default on — a Betrieb without a config row yet enforces). Judged on
    the session ROW's `amr`, which a factor confirmed from this session
    joins at once — the token's claim is what it was minted with."""

    if "mfa" in (row.amr or []):
        return False
    if AccessRole.PLATFORM_ADMIN.value in (claims.get("roles") or ()):
        return True
    enforced = db.scalar(
        select(DealershipAuthConfig.mfa_enforced).where(DealershipAuthConfig.dealership_id == uuid.UUID(claims["tenant_id"]))
    )
    return True if enforced is None else bool(enforced)


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
    path = request.url.path
    if "mfa" not in (row.amr or []) and mfa.has_factor(db, row.user_id):
        # The user enrolled a factor from another session: this one never
        # proved it and must not keep working on the password alone — nor
        # mint recovery codes. It ends; the SPA goes to sign-in on the 401.
        # Re-read first: the session that is confirming its own factor right
        # now has just written "mfa" onto this row.
        db.refresh(row)
        if "mfa" not in (row.amr or []):
            auth_sessions.revoke(db, row, reason="factor_enrolled")
            auth_events.record(
                db,
                AuthEventKind.SESSION_REVOKED,
                user_id=row.user_id,
                dealership_id=row.dealership_id,
                details={"count": 1, "reason": "factor_enrolled"},
            )
            db.commit()
            raise UnauthorizedError(_SESSION_ENDED)
    if not _enrolment_path(path, claims) and _enrolment_owed(db, claims, row):
        raise ForbiddenError(_ENROL_FIRST, details={"mfaEnrolmentRequired": True})
