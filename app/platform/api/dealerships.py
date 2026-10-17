"""Dealership + User bootstrap endpoints (issue #2, renamed WP-3).

POST /v1/dealerships is platform-admin only — it's how the first tenant gets
created. POST /v1/dealerships/{id}/users creates the initial admin User for
that Dealership (also usable by an existing dealer manager to add staff
within their own tenant). See PLANS/DMS_MDM_V1_SPEC.md §3 + the Swiss
addendum.

Dealership settings and dealership-user management (WP-2 PR-2) are manager-
only in both directions now — Roles & Permissions' matrix marks "Users of
the dealership" read as `manager`, not "Any role" the way most of the
matrix is, so this tightens list_users/get_user, not just the writes that
used to read _WRITE_ROLES.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.audit import list_tenant_audit_events
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import PageParams, page_params
from app.core.permissions import ModuleCode, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.dealership import DealershipStatus
from app.platform.models.user import UserStatus
from app.platform.schemas.dealership import DealershipCreate, DealershipPage, DealershipRead, DealershipUpdate
from app.platform.schemas.entitlement import EntitlementsRead, EntitlementsUpdate
from app.platform.schemas.membership import (
    ManagerFlagUpdate,
    MembershipCreate,
    MembershipList,
    MembershipRead,
    TextDelegationsRead,
    TextDelegationsUpdate,
)
from app.platform.schemas.user import UserCreate, UserPage, UserRead, UserUpdate
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import membership as membership_service
from app.platform.services import text_delegation as text_delegation_service
from app.platform.services import user as user_service

router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


# --- Dealership ------------------------------------------------------------


@router.post("/dealerships", response_model=DealershipRead, status_code=201)
def create_dealership(
    body: DealershipCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    dealership = dealership_service.create_dealership(db, data=body, actor_id=principal.user_id)
    return DealershipRead.model_validate(dealership, from_attributes=True)


@router.get("/dealerships/{dealership_id}", response_model=DealershipRead)
def get_dealership(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return DealershipRead.model_validate(dealership, from_attributes=True)


@router.patch("/dealerships/{dealership_id}", response_model=DealershipRead)
def update_dealership(
    dealership_id: uuid.UUID,
    body: DealershipUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = dealership_service.update_dealership(db, dealership=dealership, data=body, actor_id=principal.user_id)
    return DealershipRead.model_validate(dealership, from_attributes=True)


@router.get("/dealerships", response_model=DealershipPage)
def list_dealerships(
    status: DealershipStatus | None = None,
    dealer_group_id: uuid.UUID | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    rows, next_cursor = dealership_service.list_dealerships(
        db, params=params, status=status, dealer_group_id=dealer_group_id
    )
    return DealershipPage(
        items=[DealershipRead.model_validate(d, from_attributes=True) for d in rows], next_cursor=next_cursor
    )


@router.get("/dealerships/{dealership_id}/audit-log", response_model=AuditEventPage)
def get_dealership_audit_log(
    dealership_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    rows, next_cursor = list_tenant_audit_events(db, tenant_id=dealership_id, params=params)
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in rows], next_cursor=next_cursor
    )


# --- Module entitlements (KAN-198, D-A-04) ----------------------------------


def _entitlements_read(db: Session, dealership) -> EntitlementsRead:
    modules = entitlement_service.list_active_modules(db, dealership.id)
    return EntitlementsRead(
        modules=sorted((ModuleCode(m) for m in modules), key=lambda m: m.value),
        entitlements_version=dealership.entitlements_version,
        dealership_version=dealership.version,
    )


@router.get("/dealerships/{dealership_id}/entitlements", response_model=EntitlementsRead)
def get_entitlements(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("module_entitlements")),  # manager reads "what we bought"
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return _entitlements_read(db, dealership)


@router.put("/dealerships/{dealership_id}/entitlements", response_model=EntitlementsRead)
def set_entitlements(
    dealership_id: uuid.UUID,
    body: EntitlementsUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("module_entitlements")),  # platform_admin only (D-A-04)
    db: Session = Depends(get_db),
):
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    entitlement_service.set_entitlements(
        db, dealership=dealership, modules=set(body.modules), actor_id=principal.user_id
    )
    return _entitlements_read(db, dealership)


# --- User ----------------------------------------------------------------


@router.post("/dealerships/{dealership_id}/users", response_model=UserRead, status_code=201)
def create_user(
    dealership_id: uuid.UUID,
    body: UserCreate,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)  # 404s a well-formed but nonexistent dealership_id

    user = user_service.create_user(
        db, dealership_id=dealership_id, data=body, actor_id=principal.user_id, actor_roles=principal.roles
    )
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/dealerships/{dealership_id}/users/{user_id}", response_model=UserRead)
def get_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    return UserRead.model_validate(user, from_attributes=True)


@router.patch("/dealerships/{dealership_id}/users/{user_id}", response_model=UserRead)
def update_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    check_version(user.version, if_match, entity_name="User")
    user = user_service.update_user(db, user=user, data=body, actor_id=principal.user_id, actor_roles=principal.roles)
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/dealerships/{dealership_id}/users", response_model=UserPage)
def list_users(
    dealership_id: uuid.UUID,
    role: str | None = None,
    status: UserStatus | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    rows, next_cursor = user_service.list_users(
        db, dealership_id=dealership_id, role=role, status=status, params=params
    )
    return UserPage(items=[UserRead.model_validate(u, from_attributes=True) for u in rows], next_cursor=next_cursor)


# --- Memberships, manager flag, text delegations (KAN-199) ------------------


@router.get("/dealerships/{dealership_id}/users/{user_id}/memberships", response_model=MembershipList)
def list_memberships(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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


@router.put("/dealerships/{dealership_id}/users/{user_id}/manager-flag", response_model=UserRead)
def set_home_manager_flag(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ManagerFlagUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("manager_flag")),  # the matrix's "Granting is_dealer_manager" row
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    check_version(user.version, if_match, entity_name="User")
    user = membership_service.set_home_manager_flag(
        db, user=user, is_dealer_manager=body.is_dealer_manager, actor_id=principal.user_id, actor_roles=principal.roles
    )
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/dealerships/{dealership_id}/users/{user_id}/text-delegations", response_model=TextDelegationsRead)
def get_text_delegations(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("standard_text_delegations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)  # KAN-97: never a staff account
    _, changed = text_delegation_service.set_delegations(db, user=user, categories=set(), actor_id=principal.user_id)
    if changed:
        user.updated_by = principal.user_id
        user.version += 1
        db.commit()
