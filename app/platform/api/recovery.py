"""Platform staff's account recovery (Authentication & Identity FR-AU-25…27,
D-A-16; KAN-202) — the one door for it. Anto, 2026-10-22: every staff
recovery lives here, each with a mandatory stated reason; the staff-only
`DELETE /dealerships/{id}/users/{user_id}/sessions` and `…/mfa` are gone,
and `POST …/users/{user_id}/invitations` is the manager's re-send to a user
who never accepted (onboarding) only.

Every route is `require_staff_recovery_session`: platform staff, a session
signed in with a second factor (D-A-36), never an impersonation (FR-AU-27).
The paths name a user or a Betrieb by id because staff act across every
Betrieb (D-A-16); the tenant of the record comes from the record, never
from the caller.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, require_staff_recovery_session
from app.core.errors import BadRequestError
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.platform.api.auth import client_ip
from app.platform.models.auth_event import AuthEvent
from app.platform.models.user import User
from app.platform.schemas.recovery import (
    GrantManagerRequest,
    RecoveryRequest,
    RecoveryResult,
    RecoveryUserList,
    RecoveryUserRead,
)
from app.platform.services import dealership as dealership_service
from app.platform.services import recovery
from app.platform.services.mailer import PlatformMailer, get_mailer

router = APIRouter(tags=["platform-recovery"], route_class=IdempotentRoute)


def _user_read(db: Session, user: User) -> RecoveryUserRead:
    return RecoveryUserRead(
        id=user.id,
        dealership_id=user.tenant_id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        status=user.status,
        is_dealer_manager=user.is_dealer_manager,
        is_platform_staff=AccessRole.PLATFORM_ADMIN.value in (user.access_roles or []),
        **recovery.account_state(db, user),
    )


def _result(action: str, user: User, event: AuthEvent) -> RecoveryResult:
    return RecoveryResult(
        action=action,
        user_id=user.id,
        dealership_id=user.tenant_id,
        auth_event_id=event.id,
        occurred_at=event.occurred_at,
    )


@router.get("/platform/users", response_model=RecoveryUserList)
def search_users(
    email: str | None = Query(default=None, max_length=320),
    dealership_id: uuid.UUID | None = Query(default=None, alias="dealershipId"),
    principal: Principal = Depends(require_staff_recovery_session()),
    db: Session = Depends(get_db),
):
    """Find the account staff were asked about: by its exact e-mail address,
    by Betrieb, or both. One of the two is required."""

    if not (email and email.strip()) and dealership_id is None:
        raise BadRequestError("Search by e-mail address or Betrieb.", details={"rule": "search_criteria"})
    if dealership_id is not None:
        dealership_service.get_dealership_or_404(db, dealership_id)
    users = recovery.search_users(db, email=email if email and email.strip() else None, dealership_id=dealership_id)
    return RecoveryUserList(items=[_user_read(db, u) for u in users])


@router.post("/platform/users/{user_id}/recovery/{action}", response_model=RecoveryResult)
def recover_user(
    user_id: uuid.UUID,
    action: recovery.RecoveryAction,
    body: RecoveryRequest,
    request: Request,
    principal: Principal = Depends(require_staff_recovery_session()),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    """FR-AU-25: re-issue the invitation, reset MFA, clear the lockout or
    revoke every session — with the reason, its own auth event in the
    Betrieb's stream, an audit row and the user's `security` notice."""

    user = recovery.get_user_for_recovery_or_404(db, user_id)  # KAN-380: the user row first
    event = recovery.recover(
        db,
        mailer,
        user=user,
        action=action,
        reason=body.reason,
        actor_id=principal.user_id,
        ip_address=client_ip(request),
    )
    db.commit()
    return _result(action.value, user, event)


@router.post("/platform/dealerships/{dealership_id}/recovery/grant-manager", response_model=RecoveryResult)
def grant_manager(
    dealership_id: uuid.UUID,
    body: GrantManagerRequest,
    request: Request,
    principal: Principal = Depends(require_staff_recovery_session()),
    db: Session = Depends(get_db),
):
    """FR-AU-25: make an existing active user of this Betrieb its manager —
    at any time, with a reason (Anto, 2026-10-22)."""

    dealership_service.get_dealership_or_404(db, dealership_id)
    user = recovery.get_user_for_recovery_or_404(db, body.user_id)  # KAN-380: the user row first
    event = recovery.grant_manager(
        db,
        dealership_id=dealership_id,
        user=user,
        reason=body.reason,
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        ip_address=client_ip(request),
    )
    return _result("grant-manager", user, event)
