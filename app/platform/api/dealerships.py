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
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.audit import list_tenant_audit_events, record_audit_event
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import Principal, get_current_principal, require_access_role, require_mfa_session
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.core.object_store import ObjectStore, get_object_store
from app.core.pagination import PageParams, SortPageParams, decode_sort_cursor, page_params
from app.core.permissions import ModuleCode, require_read, require_write
from app.core.sorting import SortField, parse_sort
from app.core.tenancy import require_tenant_match
from app.db import get_db, get_session_factory
from app.platform.api.auth import client_ip
from app.platform.models.dealership import DealershipStatus
from app.platform.models.user import User, UserStatus
from app.platform.schemas.auth import (
    AuthConfigRead,
    AuthConfigUpdate,
    AuthEventList,
    AuthEventRead,
    LoginStatusRead,
)
from app.platform.schemas.dealership import DealershipCreate, DealershipPage, DealershipRead, DealershipUpdate
from app.platform.schemas.entitlement import EntitlementsRead, EntitlementsUpdate
from app.platform.schemas.me import ConnectedAccountManagerListRead, ConnectedAccountManagerRead
from app.platform.schemas.membership import (
    ManagerFlagUpdate,
    MembershipCreate,
    MembershipList,
    MembershipRead,
    TextDelegationsRead,
    TextDelegationsUpdate,
)
from app.platform.schemas.user import (
    LocationList,
    LocationRead,
    OwnedGroupRead,
    OwnershipRead,
    OwnershipTransfersRead,
    OwnershipTransfersRequest,
    UserAdminRead,
    UserCreate,
    UserDeactivatedRead,
    UserDeactivateRequest,
    UserPage,
    UserUpdate,
)
from app.platform.services import (
    auth_events,
    connected_account,
    identity,
    signature,
    user_location,
)
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import membership as membership_service
from app.platform.services import ownership as ownership_service
from app.platform.services import text_delegation as text_delegation_service
from app.platform.services import user as user_service
from app.platform.services.mailer import PlatformMailer, get_mailer

settings = get_settings()
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


@router.post("/dealerships/{dealership_id}/users", response_model=UserAdminRead, status_code=201)
def create_user(
    dealership_id: uuid.UUID,
    body: UserCreate,
    request: Request,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)  # 404s a well-formed but nonexistent dealership_id

    # FR-A-09: the user record and the invitation token are written in ONE
    # transaction — `create_user` flushes, `issue_invitation` adds the token,
    # and the commit below is the only one; a failure anywhere leaves no
    # half-created user (KAN-200 closed the two-commit gap KAN-196 left).
    user = user_service.create_user(
        db,
        dealership_id=dealership_id,
        data=body,
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        commit=False,
    )
    # KAN-196 (FR-AU-13): the invitation goes out with the user — the person
    # sets their own password through its link; nobody sets it for them.
    identity.issue_invitation(db, mailer, user=user, actor_id=principal.user_id, ip_address=client_ip(request))
    db.commit()
    return user_service.user_read(db, user)


@router.get("/dealerships/{dealership_id}/users/{user_id}", response_model=UserAdminRead)
def get_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    return user_service.user_read(db, user)


@router.patch("/dealerships/{dealership_id}/users/{user_id}", response_model=UserAdminRead)
def update_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    ip = client_ip(request)

    def on_email_change(changed: User, old_email: str) -> None:
        # KAN-196: the address is the sign-in and recovery channel — the
        # change ends tokens and sessions and tells the old address, in the
        # same transaction as the new address.
        identity.email_changed(db, mailer, user=changed, old_email=old_email, actor_id=principal.user_id, ip_address=ip)

    user = user_service.update_user(
        db,
        user=user,
        data=body,
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        on_email_change=on_email_change,
    )
    return user_service.user_read(db, user)


# U-03: only indexed or small-table columns are sortable; `user` is a few
# hundred rows per Betrieb at most, so every stored, scalar column the grid
# shows is on the list (ADR-060 + "every column sortable"); the derived
# columns (access, the sites, the line manager's name) are not.
USER_SORT_FIELDS: dict[str, object] = {
    "lastName": User.last_name,
    "firstName": User.first_name,
    "email": User.email,
    "phone": User.phone,
    "directDial": User.direct_dial,
    "deactivationReason": User.deactivation_reason,
    "status": User.status,
    "role": User.role,
    "isDealerManager": User.is_dealer_manager,
    "department": User.department,
    "employeeNumber": User.employee_number,
    "employmentStatus": User.employment_status,
    "salesAdvisorCode": User.sales_advisor_code,
    "commissionRelevant": User.commission_relevant,
    "validFrom": User.valid_from,
    "validTo": User.valid_to,
    "invitedAt": User.invited_at,
    "lastLoginAt": User.last_login_at,
    "deactivatedAt": User.deactivated_at,
    "createdAt": User.created_at,
    "updatedAt": User.updated_at,
}
_DEFAULT_USER_SORT = [
    SortField(api_name="lastName", column=User.last_name, direction="asc", nullable=False),
    SortField(api_name="firstName", column=User.first_name, direction="asc", nullable=False),
]


@router.get("/dealerships/{dealership_id}/users", response_model=UserPage)
def list_users(
    dealership_id: uuid.UUID,
    role: str | None = None,
    status: UserStatus | None = None,
    q: str | None = None,
    sort: str | None = Query(default=None, description="e.g. 'lastName:asc,lastLoginAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    sort_fields = parse_sort(sort, allowed=USER_SORT_FIELDS) or _DEFAULT_USER_SORT
    params = SortPageParams(limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields)
    rows, next_cursor = user_service.list_users(
        db, dealership_id=dealership_id, role=role, status=status, params=params, q=q
    )
    return UserPage(items=user_service.user_reads(db, rows), next_cursor=next_cursor)


# --- Locations, the deactivation step, the signature image (KAN-200) ----------


@router.get("/dealerships/{dealership_id}/locations", response_model=LocationList)
def list_locations(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("locations")),
    db: Session = Depends(get_db),
):
    """The Betrieb's sites, for the Users screen's location picker (FR-A-02).
    Read-only here: the Locations screen (Phase 2) is where they are made."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    return LocationList(
        items=[
            LocationRead(id=row.id, name=row.name, address_locality=row.address_locality)
            for row in user_location.list_locations(db, dealership_id=dealership_id)
        ]
    )


@router.get("/dealerships/{dealership_id}/users/{user_id}/ownership", response_model=OwnershipRead)
def get_user_ownership(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    """FR-A-37: what this user still owns that is open, grouped by module —
    the deactivation dialog's list, and the retry's."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    return OwnershipRead(
        groups=[
            OwnedGroupRead(module=g.module, group=g.group, count=g.count)
            for g in ownership_service.list_open(db, user=user)
        ],
        modules=ownership_service.registered_modules(),
    )


@router.post("/dealerships/{dealership_id}/users/{user_id}/ownership/transfers", response_model=OwnershipTransfersRead)
def transfer_user_ownership(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: OwnershipTransfersRequest,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
):
    """Hands the named groups to a colleague or to nobody — the retry after
    a deactivation whose hand-over failed part-way, and a plain hand-over
    for a user who stays. Each group is the owning module's own commit
    (ADR-047), audited here per group."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    transfers = ownership_service.resolve_transfers(db, user=user, requested=body.transfers, require_every_group=False)
    db.commit()  # releases the user-row lock before the modules take their own
    results, failed = ownership_service.apply_transfers(
        db, session_factory, user=user, transfers=transfers, actor_id=principal.user_id, reason=body.reason
    )
    return OwnershipTransfersRead(transfers=results, failed=failed)


@router.post("/dealerships/{dealership_id}/users/{user_id}/deactivate", response_model=UserDeactivatedRead)
def deactivate_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserDeactivateRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
):
    """FR-A-37 / D-A-35: deactivation with the reassignment step. Every
    open group needs a decision (422 names the missing ones); the last
    active manager cannot be deactivated (FR-A-13); access is cut first
    (sessions, delegations, the stamps), then each module hands its records
    over in its own transaction. There is no delete (A-RULE-5)."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    updated, results, failed = ownership_service.deactivate_user(
        db,
        session_factory,
        user=user,
        reason=body.reason,
        requested=body.transfers,
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        employment_status=body.employment_status,
    )
    return UserDeactivatedRead(user=user_service.user_read(db, updated), transfers=results, failed=failed)


@router.put("/dealerships/{dealership_id}/users/{user_id}/signature", response_model=UserAdminRead)
def put_user_signature(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    image: bytes = Body(media_type="image/png", description="The image itself: PNG or JPEG, at most 1 MiB."),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """The user's signature image (Users → Personal settings; Anto,
    2026-10-19: the manager sets it here too). The body is the image itself
    (PNG or JPEG, up to 1 MiB) with its Content-Type — never base64, never
    multipart; the row keeps the object-store key only. A plain `def` like
    every other route: the bytes arrive as a Body parameter, so the store
    write and the row lock run in the threadpool, not on the event loop."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    signature.set_signature(
        db,
        store,
        user=user,
        data=image,
        content_type=request.headers.get("content-type", ""),
        actor_id=principal.user_id,
    )
    return user_service.user_read(db, user)


@router.get("/dealerships/{dealership_id}/users/{user_id}/signature")
def get_user_signature(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    stored = signature.get_signature(store, user=user)
    return Response(content=stored.data, media_type=stored.content_type, headers={"Cache-Control": "private, no-store"})


@router.delete("/dealerships/{dealership_id}/users/{user_id}/signature", response_model=UserAdminRead)
def delete_user_signature(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    signature.remove_signature(db, store, user=user, actor_id=principal.user_id)
    return user_service.user_read(db, user)


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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user = membership_service.set_home_manager_flag(
        db, user=user, is_dealer_manager=body.is_dealer_manager, actor_id=principal.user_id, actor_roles=principal.roles
    )
    return user_service.user_read(db, user)


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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)  # KAN-97: never a staff account
    _, changed = text_delegation_service.set_delegations(db, user=user, categories=set(), actor_id=principal.user_id)
    if changed:
        user.updated_by = principal.user_id
        user.version += 1
        db.commit()


# --- Identity administration (KAN-196: FR-AU-10/11/12/17/21/25) -------------------------


@router.post("/dealerships/{dealership_id}/users/{user_id}/invitations", status_code=202, dependencies=[Depends(require_mfa_session())])
def reissue_invitation(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    """FR-AU-13: a fresh single-use invitation (the previous one is
    consumed) — never a password — for a user still `invited` (onboarding,
    the manager's own business). For an account that is no longer `invited`
    a new invitation is account recovery: platform staff's, with a stated
    reason, at `POST /v1/platform/users/{id}/recovery/reissue-invitation`
    (KAN-202; Anto, 2026-10-22: one door for recovery)."""

    require_tenant_match(dealership_id, principal)
    # The user row before the tokens (KAN-380/KAN-400): the invitation stamps
    # `invited_at` (KAN-200) after ending the live invitations, and an
    # acceptance holds the user row before it consumes its token.
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    if user.status != UserStatus.INVITED:
        # D-A-16 / FR-AU-25: once the account is no longer `invited`, a new
        # invitation is a recovery — platform staff's, with a reason, at the
        # recovery door; staff too go there (KAN-202).
        raise ForbiddenError(
            "Re-inviting a user who already accepted is account recovery, which platform staff handle.",
            details={"recovery": "reissue-invitation"},
        )
    identity.issue_invitation(db, mailer, user=user, actor_id=principal.user_id, ip_address=client_ip(request))
    db.commit()


@router.get(
    "/dealerships/{dealership_id}/users/{user_id}/connected-accounts", response_model=ConnectedAccountManagerListRead
)
def list_user_connected_accounts(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("connected_accounts")),
    db: Session = Depends(get_db),
):
    """FR-AU-24 / D-A-15: the manager sees that a user's grant exists — its
    provider, scopes and status, never the account's address or its usage,
    and never mail or calendar content (nothing here can reach it). Linking
    is the user's own, in My account."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    rows = connected_account.list_for_user(db, user_id=user.id)
    return ConnectedAccountManagerListRead(
        items=[ConnectedAccountManagerRead.model_validate(r, from_attributes=True) for r in rows]
    )


@router.delete(
    "/dealerships/{dealership_id}/users/{user_id}/connected-accounts/{account_id}",
    response_model=ConnectedAccountManagerRead,
)
def revoke_user_connected_account(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("connected_accounts")),
    db: Session = Depends(get_db),
):
    """The manager may revoke a user's grant (D-A-15: "may revoke only");
    the row stays, revoked — the user sees it was a manager
    (`revokedReason`), the Betrieb's auth events name who."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    account = connected_account.get_for_update_or_404(db, user_id=user.id, account_id=account_id)
    check_version(account.version, if_match, entity_name="ConnectedAccount")
    connected_account.revoke(
        db, account=account, user=user, actor_id=principal.user_id, reason=connected_account.REASON_MANAGER
    )
    db.commit()
    connected_account.end_at_providers([account])
    db.refresh(account)
    return ConnectedAccountManagerRead.model_validate(account, from_attributes=True)


@router.get("/dealerships/{dealership_id}/users/{user_id}/login-status", response_model=LoginStatusRead, dependencies=[Depends(require_mfa_session())])
def get_login_status(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    """FR-AU-10: the manager sees that a user is locked out; nothing more —
    and never a staff account's (KAN-97)."""

    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    return LoginStatusRead(**identity.login_status(db, user))


@router.get("/dealerships/{dealership_id}/auth-events", response_model=AuthEventList)
def list_auth_events(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    """FR-AU-21: the manager reads the authentication stream of his own
    Betrieb — the newest 200 events."""

    require_tenant_match(dealership_id, principal)
    rows = auth_events.list_for_dealership(db, dealership_id=dealership_id)
    return AuthEventList(
        items=[
            AuthEventRead(
                id=row.id,
                kind=row.kind.value,
                user_id=row.user_id,
                actor_id=row.actor_id,
                ip_address=row.ip_address,
                occurred_at=row.occurred_at,
                details=row.details,
            )
            for row in rows
        ]
    )


@router.get("/dealerships/{dealership_id}/auth-config", response_model=AuthConfigRead)
def get_auth_config(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    config = identity.read_auth_config(db, dealership_id)
    return AuthConfigRead(auth_method=config.auth_method.value, mfa_enforced=config.mfa_enforced, version=config.version)


@router.put(
    "/dealerships/{dealership_id}/auth-config",
    response_model=AuthConfigRead,
    dependencies=[Depends(require_mfa_session())],
)
def set_auth_config(
    dealership_id: uuid.UUID,
    body: AuthConfigUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    """D-A-09 / FR-AU-11: the manager decides whether his Betrieb enforces a
    second factor. Platform staff are always enforced (D-A-36), whatever
    this says."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    config = identity.auth_config_for(db, dealership_id)
    check_version(config.version, if_match, entity_name="DealershipAuthConfig")
    if config.mfa_enforced != body.mfa_enforced:
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=dealership_id,
            tenant_id=dealership_id,
            action="auth_config_set",
            actor_id=principal.user_id,
            before={"mfa_enforced": config.mfa_enforced},
            after={"mfa_enforced": body.mfa_enforced},
        )
        config.mfa_enforced = body.mfa_enforced
        config.updated_by = principal.user_id
        config.version += 1
    db.commit()
    return AuthConfigRead(
        auth_method=config.auth_method.value, mfa_enforced=config.mfa_enforced, version=config.version
    )
