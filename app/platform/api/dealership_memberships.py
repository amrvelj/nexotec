"""Memberships, the manager flag and text delegations of a dealership's users
(KAN-199), split from `dealerships.py` (KAN-668, a pure move). Mounted after
`dealership_users.py` in `app/api/v1/__init__.py`.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import (
    require_read,
    require_write,
)
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.schemas.membership import (
    ManagerFlagUpdate,
    MembershipCreate,
    MembershipList,
    MembershipRead,
    TextDelegationsRead,
    TextDelegationsUpdate,
)
from app.platform.schemas.user import (
    UserAdminRead,
)
from app.platform.services import membership as membership_service
from app.platform.services import text_delegation as text_delegation_service
from app.platform.services import user as user_service
from app.platform.services import user_reads as user_reads_service

router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


# --- Memberships, manager flag, text delegations (KAN-199) ------------------


@router.get("/dealerships/{dealership_id}/users/{user_id}/memberships", response_model=MembershipList)
def list_memberships(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    return MembershipList(
        items=membership_service.list_memberships(
            db, user=user, actor_id=principal.user_id, actor_roles=principal.roles
        ),
        user_version=user.version,
    )


@router.post("/dealerships/{dealership_id}/users/{user_id}/memberships", response_model=MembershipRead, status_code=201)
def grant_membership(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MembershipCreate,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    row = membership_service.grant_membership(
        db, user=user, data=body, actor_id=principal.user_id, actor_roles=principal.roles
    )
    return membership_service.read_membership(db, row)


@router.delete("/dealerships/{dealership_id}/users/{user_id}/memberships/{member_dealership_id}", status_code=204)
def end_membership(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    member_dealership_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    membership_service.end_membership(
        db, user=user, dealership_id=member_dealership_id, actor_id=principal.user_id, actor_roles=principal.roles
    )


@router.put(
    "/dealerships/{dealership_id}/users/{user_id}/memberships/{member_dealership_id}/manager-flag",
    response_model=MembershipRead,
)
def set_membership_manager_flag(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    member_dealership_id: uuid.UUID,
    body: ManagerFlagUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("manager_flag")),  # the matrix's "Granting is_dealer_manager" row
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    row = membership_service.set_membership_manager_flag(
        db,
        user=user,
        dealership_id=member_dealership_id,
        is_dealer_manager=body.is_dealer_manager,
        actor_id=principal.user_id,
        actor_roles=principal.roles,
    )
    return membership_service.read_membership(db, row)


@router.put("/dealerships/{dealership_id}/users/{user_id}/manager-flag", response_model=UserAdminRead)
def set_home_manager_flag(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ManagerFlagUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("manager_flag")),  # the matrix's "Granting is_dealer_manager" row
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user = membership_service.set_home_manager_flag(
        db, user=user, is_dealer_manager=body.is_dealer_manager, actor_id=principal.user_id, actor_roles=principal.roles
    )
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


@router.get("/dealerships/{dealership_id}/users/{user_id}/text-delegations", response_model=TextDelegationsRead)
def get_text_delegations(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("standard_text_delegations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    return TextDelegationsRead(
        categories=text_delegation_service.list_active_categories(db, user_id=user.id), user_version=user.version
    )


@router.put("/dealerships/{dealership_id}/users/{user_id}/text-delegations", response_model=TextDelegationsRead)
def set_text_delegations(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: TextDelegationsUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("standard_text_delegations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)  # KAN-97: never a staff account
    user_service.assert_user_is_not_deactivated(user)
    categories, changed = text_delegation_service.set_delegations(
        db, user=user, categories=set(body.categories), actor_id=principal.user_id
    )
    if changed:
        user.updated_by = principal.user_id
        user.version += 1
        db.commit()
        db.refresh(user)
    return TextDelegationsRead(categories=categories, user_version=user.version)


@router.delete("/dealerships/{dealership_id}/users/{user_id}/text-delegations", status_code=204)
def end_text_delegations(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("standard_text_delegations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)  # KAN-97: never a staff account
    _, changed = text_delegation_service.set_delegations(db, user=user, categories=set(), actor_id=principal.user_id)
    if changed:
        user.updated_by = principal.user_id
        user.version += 1
        db.commit()
