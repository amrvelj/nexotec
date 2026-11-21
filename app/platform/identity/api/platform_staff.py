"""The Platform users panel (KAN-221 PR-6; FR-A-15, D-A-05, ADR-188):
Nexotec staff, read and administered by staff
(`services/platform_staff.py`). Staff only; grant and revoke need a session
signed in with a second factor (D-A-36) and are never done from inside a
support session (FR-A-17)."""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import require_if_match
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.core.impersonation import refuse_under_impersonation
from app.core.pagination import PageParams, page_params
from app.db import get_db, get_session_factory
from app.platform.identity.api.auth import client_ip
from app.platform.identity.schemas.platform_staff import (
    PlatformStaffCreate,
    PlatformStaffEntry,
    PlatformStaffPage,
    PlatformStaffRevoke,
    PlatformStaffRevoked,
)
from app.platform.identity.services import identity
from app.platform.identity.services import platform_staff as staff_service
from app.platform.reference.services.mailer import PlatformMailer, get_mailer

router = APIRouter(tags=["platform-staff"], route_class=IdempotentRoute)


def require_staff_with_factor(principal: Principal = Depends(get_current_principal)) -> Principal:
    if AccessRole.PLATFORM_ADMIN not in principal.roles:
        raise ForbiddenError("The Platform users panel is for platform staff.")
    if not principal.mfa:
        raise ForbiddenError("This action needs a staff session signed in with a second factor.")
    return principal


@router.get(
    "/platform/staff",
    response_model=PlatformStaffPage,
    description=(
        "KAN-221 PR-6: every holder of platform_admin — staff homed in the platform tenant, and legacy "
        "holders still homed in a dealer's Betrieb (`inPlatformTenant` false) — with the second-factor state "
        "and the running support session. Staff only."
    ),
)
def list_platform_staff(
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    items, next_cursor = staff_service.list_staff(db, params=params)
    return PlatformStaffPage(items=items, next_cursor=next_cursor)


@router.post(
    "/platform/staff",
    dependencies=[Depends(refuse_under_impersonation)],
    response_model=PlatformStaffEntry,
    status_code=201,
    description=(
        "KAN-221 PR-6 (ADR-188): grant platform_admin — a new staff account in the platform tenant, invited by "
        "e-mail. Staff with a second factor only; never under a support session."
    ),
)
def create_platform_staff(
    body: PlatformStaffCreate,
    request: Request,
    principal: Principal = Depends(require_staff_with_factor),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    user = staff_service.create_staff(db, data=body, principal=principal)
    identity.issue_invitation(db, mailer, user=user, actor_id=principal.user_id, ip_address=client_ip(request))
    db.commit()
    return staff_service.entries_for(db, [user])[0]


@router.post(
    "/platform/staff/{user_id}/revoke",
    dependencies=[Depends(refuse_under_impersonation)],
    response_model=PlatformStaffRevoked,
    description=(
        "KAN-221 PR-6 (ADR-188): revoke platform_admin, with a reason. A staff account in the platform tenant "
        "is deactivated (it holds nothing else); a legacy holder keeps their Betrieb account without the role "
        "(422 `staff_role_is_the_only_role` when it is their only one). Never oneself (422 "
        "`platform_admin_self_revoke`), never the last active holder (422 `last_platform_admin`). If-Match."
    ),
)
def revoke_platform_staff(
    user_id: uuid.UUID,
    body: PlatformStaffRevoke,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_staff_with_factor),
    db: Session = Depends(get_db),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
):
    outcome = staff_service.revoke_staff(
        db, session_factory, user_id=user_id, if_match=if_match, reason=body.reason, principal=principal
    )
    return PlatformStaffRevoked(entry=staff_service.entries_for(db, [outcome.user])[0], deactivated=outcome.deactivated)
