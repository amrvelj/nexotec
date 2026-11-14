"""Dealership-user administration endpoints, split from `dealerships.py`
(KAN-668, a pure move): create, read, update and list the users of a
Betrieb, the deactivation step with its ownership hand-over, the signature
image and the photo.

Memberships, the manager flag and text delegations are in
`dealership_memberships.py`; invitations, connected accounts, the login
status and the Betrieb's sign-in policy in `dealership_identity.py`. The
routers are mounted one after the other in `app/api/v1/__init__.py`, in the
order the routes had in the one file.
"""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.object_store import ObjectStore, get_object_store
from app.core.pagination import SortPageParams
from app.core.permissions import (
    require_read,
    require_write,
)
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.core.tenancy import require_tenant_match
from app.db import get_db, get_session_factory
from app.platform.api.auth import client_ip
from app.platform.models.user import User, UserStatus
from app.platform.organisation.services import dealership as dealership_service
from app.platform.reference.services.mailer import PlatformMailer, get_mailer
from app.platform.schemas.user import (
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
    identity,
    photo,
    signature,
)
from app.platform.services import ownership as ownership_service
from app.platform.services import user as user_service
from app.platform.services import user_reads as user_reads_service

settings = get_settings()
router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


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
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


@router.get("/dealerships/{dealership_id}/users/{user_id}", response_model=UserAdminRead)
def get_user(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


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
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


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
    "correspondenceLanguage": User.correspondence_language,
    "signatureBlock": User.signature_block,
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
    params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)
    rows, next_cursor = user_reads_service.list_users(
        db, dealership_id=dealership_id, role=role, status=status, params=params, q=q
    )
    return UserPage(items=user_reads_service.user_reads(db, rows, viewer_id=principal.user_id, viewer_roles=principal.roles), next_cursor=next_cursor)


# --- The deactivation step, the signature image (KAN-200; locations moved to api/locations.py, KAN-204)


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
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    return UserDeactivatedRead(user=user_reads_service.user_read(db, updated, viewer_id=principal.user_id, viewer_roles=principal.roles), transfers=results, failed=failed)


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
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
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
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


@router.get("/dealerships/{dealership_id}/users/{user_id}/signature")
def get_user_signature(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
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
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    signature.remove_signature(db, store, user=user, actor_id=principal.user_id)
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


@router.put("/dealerships/{dealership_id}/users/{user_id}/photo", response_model=UserAdminRead)
def put_user_photo(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    image: bytes = Body(media_type="image/jpeg", description="The image itself: PNG or JPEG, at most 5 MiB."),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """The user's photo (KAN-401, Users → Fields), set like the signature
    image: the body is the image itself with its Content-Type, the row keeps
    the object-store key only."""

    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    photo.set_photo(
        db,
        store,
        user=user,
        data=image,
        content_type=request.headers.get("content-type", ""),
        actor_id=principal.user_id,
    )
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)


@router.get("/dealerships/{dealership_id}/users/{user_id}/photo")
def get_user_photo(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    stored = photo.get_photo(store, user=user)
    return Response(content=stored.data, media_type=stored.content_type, headers={"Cache-Control": "private, no-store"})


@router.delete("/dealerships/{dealership_id}/users/{user_id}/photo", response_model=UserAdminRead)
def delete_user_photo(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    check_version(user.version, if_match, entity_name="User")
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    photo.remove_photo(db, store, user=user, actor_id=principal.user_id)
    return user_reads_service.user_read(db, user, viewer_id=principal.user_id, viewer_roles=principal.roles)
