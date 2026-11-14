"""Platform Administration › Onboarding (KAN-210; D-A-06, FR-A-24/25).

`POST /v1/platform/onboarding` — platform staff onboard a Betrieb in one
atomic step (`services/onboarding.py`); the older `POST /v1/dealerships`
stays for API clients, deprecated. `GET /v1/platform/dealer-groups` lists the
groups the wizard's first step offers — searched by name and cursor-paginated
(KAN-652), never the whole table in one answer.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_access_role, require_mfa_session
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.platform.api.auth import client_ip
from app.platform.identity.schemas.onboarding import (
    DealerGroupOption,
    DealerGroupOptionList,
    OnboardingRequest,
    OnboardingResult,
)
from app.platform.identity.services import onboarding as onboarding_service
from app.platform.organisation.services import dealer_group_options
from app.platform.reference.services.mailer import PlatformMailer, get_mailer

router = APIRouter(tags=["platform-onboarding"], route_class=IdempotentRoute)


@router.post(
    "/platform/onboarding",
    response_model=OnboardingResult,
    status_code=201,
    dependencies=[Depends(require_mfa_session())],
)
def onboard_dealership(
    body: OnboardingRequest,
    request: Request,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    """Group, legal entity (UID unique: 409 `uid_already_onboarded`),
    entitlements, first manager — one transaction, nothing commercial."""

    done = onboarding_service.onboard(
        db, data=body, actor_id=principal.user_id, mailer=mailer, ip_address=client_ip(request)
    )
    return OnboardingResult(
        dealership_id=done.dealership.id,
        dealer_group_id=done.dealership.dealer_group_id,
        status=done.dealership.status.value,
        entitlements=sorted(done.entitlements),
        manager_user_id=done.manager.id,
        manager_email=done.manager.email,
        manager_status=done.manager.status.value,
        invitation_delivered=done.invitation_delivered,
        location_id=done.location.id,
    )


@router.get("/platform/dealer-groups", response_model=DealerGroupOptionList)
def list_dealer_groups(
    q: str | None = Query(default=None, max_length=200, description="Name contains (case-insensitive)."),
    limit: int = Query(
        default=get_settings().pagination_default_limit, ge=1, le=get_settings().pagination_max_limit
    ),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """The groups whose name contains `q`, by name, one page at a time
    (KAN-652): the wizard's group picker asks as the user types."""

    rows, next_cursor = dealer_group_options.list_dealer_group_options(
        db, q=q, limit=limit, cursor=cursor
    )
    return DealerGroupOptionList(
        items=[DealerGroupOption(id=row.id, name=row.name) for row in rows], next_cursor=next_cursor
    )
