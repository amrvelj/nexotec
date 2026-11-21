"""Support sessions (KAN-221): start and end (PR 2), and the support-session
log (PR 5) — the Betrieb's own for its manager, every Betrieb's for platform
staff. The record only — the token that acts for the user is PR 3
(`services/support_sessions.py`)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.config import get_settings
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.core.impersonation import refuse_under_impersonation
from app.core.pagination import PageParams, SortPageParams, decode_cursor
from app.core.permissions import require_read
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.schemas.support_session import (
    MySupportSessionRead,
    SupportSessionLogPage,
    SupportSessionRead,
    SupportSessionStart,
)
from app.platform.identity.services import support_sessions

router = APIRouter(tags=["platform-support-sessions"], route_class=IdempotentRoute)
_settings = get_settings()

# PR 6: the log screens read newest first. Without `sort` the log keeps the
# shared oldest-first cursor it shipped with (PR 5).
SUPPORT_SESSION_SORT_FIELDS = {"startedAt": SupportSession.started_at}
_SORT_DESCRIPTION = "`startedAt:desc` for newest first (the log screens); without it, oldest first."


def _log_params(limit: int, sort: str | None, cursor: str | None) -> PageParams | SortPageParams:
    sort_fields: list[SortField] = parse_sort(sort, allowed=SUPPORT_SESSION_SORT_FIELDS)
    if not sort_fields:
        return PageParams(limit=limit, cursor=decode_cursor(cursor) if cursor else None)
    return SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)


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
    dependencies=[Depends(refuse_under_impersonation)],
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


@router.get(
    "/dealerships/{dealership_id}/support-sessions",
    response_model=SupportSessionLogPage,
    description=(
        "KAN-221 (FR-A-15, the matrix row «Support-session log»): every support session Nexotec staff held in "
        "this Betrieb, running or over, with the staff member, the user, the reason and how it ended. The "
        "manager reads their own Betrieb's; another Betrieb's is 404. Each start and end is also in the "
        "Betrieb's audit log."
    ),
)
def list_dealership_support_sessions(
    dealership_id: uuid.UUID,
    sort: str | None = Query(default=None, description=_SORT_DESCRIPTION),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_settings.pagination_default_limit, ge=1, le=_settings.pagination_max_limit),
    principal: Principal = Depends(require_read("support_session_log")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    items, next_cursor = support_sessions.list_support_sessions(
        db, params=_log_params(limit, sort, cursor), dealership_id=dealership_id
    )
    return SupportSessionLogPage(items=items, next_cursor=next_cursor)


@router.get(
    "/platform/support-sessions",
    response_model=SupportSessionLogPage,
    description=(
        "KAN-221 (FR-A-15): the platform-wide support-session log, for platform staff only. Narrowed by "
        "`dealershipId` and `staffUserId`."
    ),
)
def list_platform_support_sessions(
    dealership_id: uuid.UUID | None = Query(default=None, alias="dealershipId"),
    staff_user_id: uuid.UUID | None = Query(default=None, alias="staffUserId"),
    sort: str | None = Query(default=None, description=_SORT_DESCRIPTION),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_settings.pagination_default_limit, ge=1, le=_settings.pagination_max_limit),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    items, next_cursor = support_sessions.list_support_sessions(
        db, params=_log_params(limit, sort, cursor), dealership_id=dealership_id, staff_user_id=staff_user_id
    )
    return SupportSessionLogPage(items=items, next_cursor=next_cursor)


@router.get(
    "/me/support-session",
    response_model=MySupportSessionRead,
    description=(
        "KAN-221 PR-6 (FR-A-18, D-A-05): the support session running on the caller, for the banner — as the "
        "user it supports, or as the staff member acting for them. `session` is null when none runs. The SPA "
        "asks on focus and every minute (no Web Push adapter yet)."
    ),
)
def get_my_support_session(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    return support_sessions.running_session_for(db, principal)
