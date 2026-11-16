"""Support sessions (KAN-221 PR-2): start and end. The record only — the
token that acts for the user is PR 3 (`services/support_sessions.py`)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.platform.identity.schemas.support_session import SupportSessionRead, SupportSessionStart
from app.platform.identity.services import support_sessions

router = APIRouter(tags=["platform-support-sessions"], route_class=IdempotentRoute)


def require_staff_support_session(principal: Principal = Depends(get_current_principal)) -> Principal:
    """D-A-05, D-A-36, FR-A-17: platform staff, from a session signed in
    with a second factor, never from inside another impersonation. Anyone
    else is 403 — the route names no tenant's record before this runs."""

    if AccessRole.PLATFORM_ADMIN not in principal.roles:
        raise ForbiddenError("Support sessions are for platform staff.")
    if not principal.mfa:
        raise ForbiddenError("This action needs a staff session signed in with a second factor.")
    if principal.acting_for is not None:
        raise ForbiddenError("A support session cannot be started from inside one.")
    return principal


@router.post(
    "/platform/support-sessions",
    response_model=SupportSessionRead,
    status_code=201,
    description=(
        "KAN-221: a platform staff member starts a 60-minute support session for an active user, with a "
        "mandatory reason. Refused for a platform_admin (422 `support_target_platform_admin`), oneself "
        "(`support_target_self`), a user who is not active (`support_target_not_active`), and while the staff "
        "member's previous session still runs (409 `support_session_already_open`). The token that acts for "
        "the user is not built yet: the session is recorded and audited, and grants nothing."
    ),
)
def start_support_session(
    body: SupportSessionStart,
    principal: Principal = Depends(require_staff_support_session),
    db: Session = Depends(get_db),
):
    row = support_sessions.start_support_session(
        db, staff=principal, target_user_id=body.target_user_id, reason=body.reason
    )
    return support_sessions.to_read(row)


@router.post(
    "/platform/support-sessions/{session_id}/end",
    response_model=SupportSessionRead,
    description=(
        "KAN-221 (FR-A-18): the staff member or the user ends a running session. Anyone else gets 404; a "
        "session already over is 409 `support_session_ended`."
    ),
)
def end_support_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    row = support_sessions.end_support_session(db, session_id=session_id, principal=principal)
    return support_sessions.to_read(row)
