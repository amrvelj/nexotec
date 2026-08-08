"""Customer endpoints (issue #4).

Unlike Dealer/User, these routes are flat (`/v1/customers`, not
`/v1/dealers/{id}/customers`) — the spec's literal endpoint list has no
dealer_id path segment, and Customer is "not shared cross-tenant in v1"
(spec §1), so tenant is resolved purely from the JWT (principal.tenant_id),
with no path param to validate against. This also means platform_admin has
no special cross-tenant reach here (unlike Dealer, where require_tenant_match
explicitly lets platform_admin bypass the match) — Round 3's access-control
notes scope platform_admin's cross-tenant power to "dealer onboarding only,"
and Customer isn't part of that.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.models.customer import Customer, CustomerLifecycleStatus
from app.schemas.audit import AuditEventPage, AuditEventRead
from app.schemas.customer import (
    CustomerCreate,
    CustomerDuplicateCandidate,
    CustomerDuplicateCandidateList,
    CustomerEmailCreate,
    CustomerEmailPage,
    CustomerEmailRead,
    CustomerEmailUpdate,
    CustomerExternalIdCreate,
    CustomerExternalIdPage,
    CustomerExternalIdRead,
    CustomerExternalIdUpdate,
    CustomerMergeRequest,
    CustomerPage,
    CustomerPhoneCreate,
    CustomerPhonePage,
    CustomerPhoneRead,
    CustomerPhoneUpdate,
    CustomerRead,
    CustomerUpdate,
    CustomerVehicleCreate,
    CustomerVehiclePage,
    CustomerVehicleRead,
    CustomerVehicleUpdate,
)
from app.services import customer as customer_service
from app.services import dealer as dealer_service
from app.services.audit import list_audit_events
from app.services.idempotency import find_cached_response, store_response

router = APIRouter(tags=["customers"])
settings = get_settings()

_WRITE_ROLES = (AccessRole.DEALER_ADMIN, AccessRole.SALES)

# U-02/U-03: only columns with a supporting index are offered as sortable —
# see alembic/versions/d2f7b0e9c453_customer_sort_indexes.py for last_name/
# updated_at/created_at; customer_number and company_name were already
# indexed (D-06). Keys are the API-facing (camelCase) field name, exactly
# as the UI/UX doc's own saved-view example uses them
# (`{"field": "lastName", "direction": "asc"}`).
CUSTOMER_SORT_FIELDS: dict[str, object] = {
    "customerNumber": Customer.customer_number,
    "companyName": Customer.company_name,
    "lastName": Customer.last_name,
    "updatedAt": Customer.updated_at,
    "createdAt": Customer.created_at,
}
# "Default sort: Declared per grid... For most grids: updatedAt:desc"
# (UI/UX Core Principles FR-UI-01) — used when the client sends no `sort`.
_DEFAULT_CUSTOMER_SORT = [
    SortField(api_name="updatedAt", column=Customer.updated_at, direction="desc", nullable=False)
]


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


@router.post("/customers", response_model=CustomerRead, status_code=201)
def create_customer(
    body: CustomerCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    # Tenant is JWT-derived here (no path param to validate — see module
    # docstring), so unlike Dealer/User this 404 guards against a token
    # whose tenant_id claim doesn't match a real Dealer, catching that as a
    # clean 404 instead of an unhandled FK-violation 500 on insert.
    dealer_service.get_dealer_or_404(db, principal.tenant_id)

    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    customer = customer_service.create_customer(
        db, tenant_id=principal.tenant_id, data=body, actor_id=principal.user_id
    )
    result = CustomerRead.model_validate(customer, from_attributes=True)

    if idempotency_key:
        store_response(
            db,
            tenant_id=principal.tenant_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.get("/customers/duplicate-check", response_model=CustomerDuplicateCandidateList)
def duplicate_check(
    q: str = Query(min_length=1),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = customer_service.duplicate_check(db, tenant_id=principal.tenant_id, q=q)
    # Plain dicts, not ORM rows — see services.customer.duplicate_check for
    # why a candidate is not simply a Customer.
    return CustomerDuplicateCandidateList(
        items=[CustomerDuplicateCandidate.model_validate(row) for row in rows]
    )


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    return CustomerRead.model_validate(customer, from_attributes=True)


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    check_version(customer.version, if_match, entity_name="Customer")
    customer = customer_service.update_customer(db, customer=customer, data=body, actor_id=principal.user_id)
    return CustomerRead.model_validate(customer, from_attributes=True)


@router.post("/customers/{customer_id}/merge", response_model=CustomerRead)
def merge_customer(
    customer_id: uuid.UUID,
    body: CustomerMergeRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    check_version(customer.version, if_match, entity_name="Customer")
    customer = customer_service.merge_customer(
        db,
        customer=customer,
        duplicate_of_customer_id=body.duplicate_of_customer_id,
        actor_id=principal.user_id,
    )
    return CustomerRead.model_validate(customer, from_attributes=True)


@router.get("/customers", response_model=CustomerPage)
def list_customers(
    q: str | None = None,
    lifecycle_status: CustomerLifecycleStatus | None = None,
    updated_since: dt.datetime | None = None,
    include_merged: bool = False,
    sort: str | None = Query(default=None, description="e.g. 'lastName:asc,updatedAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=CUSTOMER_SORT_FIELDS) or _DEFAULT_CUSTOMER_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = customer_service.list_customers(
        db,
        tenant_id=principal.tenant_id,
        q=q,
        lifecycle_status=lifecycle_status,
        updated_since=updated_since,
        params=params,
        include_merged=include_merged,
    )
    return CustomerPage(
        items=[CustomerRead.model_validate(c, from_attributes=True) for c in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/customers/{customer_id}/audit-log", response_model=AuditEventPage)
def get_customer_audit_log(
    customer_id: uuid.UUID,
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN, AccessRole.AUDITOR)),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    events = list_audit_events(
        db, entity_type="customer", entity_id=customer_id, tenant_id=principal.tenant_id
    )
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in events], next_cursor=None
    )


# --- CustomerPhone / CustomerEmail: multi-valued contact details (Customer
# PRD, 2026-08-07). Same _WRITE_ROLES as the parent Customer; no If-Match
# on these child rows — no version column (CTO ruling: is_primary isn't a
# high-contention field), same reasoning as VehicleCustodyEvent being
# unversioned.


@router.get("/customers/{customer_id}/phones", response_model=CustomerPhonePage)
def list_customer_phones(
    customer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    rows = customer_service.list_customer_phones(db, customer_id=customer_id)
    return CustomerPhonePage(items=[CustomerPhoneRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/customers/{customer_id}/phones", response_model=CustomerPhoneRead, status_code=201)
def create_customer_phone(
    customer_id: uuid.UUID,
    body: CustomerPhoneCreate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    phone = customer_service.create_customer_phone(db, customer=customer, data=body, actor_id=principal.user_id)
    return CustomerPhoneRead.model_validate(phone, from_attributes=True)


@router.patch("/customers/{customer_id}/phones/{phone_id}", response_model=CustomerPhoneRead)
def update_customer_phone(
    customer_id: uuid.UUID,
    phone_id: uuid.UUID,
    body: CustomerPhoneUpdate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    phone = customer_service.get_customer_phone_or_404(
        db, tenant_id=principal.tenant_id, customer_id=customer_id, phone_id=phone_id
    )
    phone = customer_service.update_customer_phone(db, phone=phone, data=body, actor_id=principal.user_id)
    return CustomerPhoneRead.model_validate(phone, from_attributes=True)


@router.delete("/customers/{customer_id}/phones/{phone_id}", status_code=204)
def delete_customer_phone(
    customer_id: uuid.UUID,
    phone_id: uuid.UUID,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    phone = customer_service.get_customer_phone_or_404(
        db, tenant_id=principal.tenant_id, customer_id=customer_id, phone_id=phone_id
    )
    customer_service.delete_customer_phone(db, phone=phone, actor_id=principal.user_id)


@router.get("/customers/{customer_id}/emails", response_model=CustomerEmailPage)
def list_customer_emails(
    customer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    rows = customer_service.list_customer_emails(db, customer_id=customer_id)
    return CustomerEmailPage(items=[CustomerEmailRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/customers/{customer_id}/emails", response_model=CustomerEmailRead, status_code=201)
def create_customer_email(
    customer_id: uuid.UUID,
    body: CustomerEmailCreate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    email = customer_service.create_customer_email(db, customer=customer, data=body, actor_id=principal.user_id)
    return CustomerEmailRead.model_validate(email, from_attributes=True)


@router.patch("/customers/{customer_id}/emails/{email_id}", response_model=CustomerEmailRead)
def update_customer_email(
    customer_id: uuid.UUID,
    email_id: uuid.UUID,
    body: CustomerEmailUpdate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    email = customer_service.get_customer_email_or_404(
        db, tenant_id=principal.tenant_id, customer_id=customer_id, email_id=email_id
    )
    email = customer_service.update_customer_email(db, email=email, data=body, actor_id=principal.user_id)
    return CustomerEmailRead.model_validate(email, from_attributes=True)


@router.delete("/customers/{customer_id}/emails/{email_id}", status_code=204)
def delete_customer_email(
    customer_id: uuid.UUID,
    email_id: uuid.UUID,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    email = customer_service.get_customer_email_or_404(
        db, tenant_id=principal.tenant_id, customer_id=customer_id, email_id=email_id
    )
    customer_service.delete_customer_email(db, email=email, actor_id=principal.user_id)


# --- CustomerExternalId: per-dealer CRM/OEM linkage. Write is
# platform_admin-only (Anto's ruling, 2026-08-07, overriding the earlier
# dealer_admin default) — read stays open to any authenticated tenant role,
# same pattern as an audit-log endpoint being readable but not writable by
# regular roles.


@router.get("/customers/{customer_id}/external-ids", response_model=CustomerExternalIdPage)
def list_customer_external_ids(
    customer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    rows = customer_service.list_customer_external_ids(db, customer_id=customer_id)
    return CustomerExternalIdPage(
        items=[CustomerExternalIdRead.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post("/customers/{customer_id}/external-ids", response_model=CustomerExternalIdRead, status_code=201)
def create_customer_external_id(
    customer_id: uuid.UUID,
    body: CustomerExternalIdCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    # Tenant-agnostic lookup, not get_customer_or_404(principal.tenant_id,
    # ...): platform_admin's principal.tenant_id is a synthetic claim, not a
    # real dealer — and unlike Dealer's, Customer's own module docstring
    # explicitly scopes platform_admin's cross-tenant reach to "dealer
    # onboarding only." CustomerExternalId is the one deliberate exception
    # to that (Anto's ruling, 2026-08-07: this is a platform-managed CRM/OEM
    # linkage, by design reachable across every dealer) — resolve the
    # customer's real tenant_id from the row itself instead.
    customer = customer_service.get_customer_by_id_or_404(db, customer_id)
    row = customer_service.create_customer_external_id(db, customer=customer, data=body, actor_id=principal.user_id)
    return CustomerExternalIdRead.model_validate(row, from_attributes=True)


@router.patch("/customers/{customer_id}/external-ids/{external_id_row_id}", response_model=CustomerExternalIdRead)
def update_customer_external_id(
    customer_id: uuid.UUID,
    external_id_row_id: uuid.UUID,
    body: CustomerExternalIdUpdate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_by_id_or_404(db, customer_id)
    row = customer_service.get_customer_external_id_or_404(
        db, tenant_id=customer.tenant_id, customer_id=customer_id, external_id_row_id=external_id_row_id
    )
    row = customer_service.update_customer_external_id(db, row=row, data=body, actor_id=principal.user_id)
    return CustomerExternalIdRead.model_validate(row, from_attributes=True)


@router.delete("/customers/{customer_id}/external-ids/{external_id_row_id}", status_code=204)
def delete_customer_external_id(
    customer_id: uuid.UUID,
    external_id_row_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_by_id_or_404(db, customer_id)
    row = customer_service.get_customer_external_id_or_404(
        db, tenant_id=customer.tenant_id, customer_id=customer_id, external_id_row_id=external_id_row_id
    )
    customer_service.delete_customer_external_id(db, row=row, actor_id=principal.user_id)


# --- Customer<->Vehicle relationships (D-12, FR-10): owner/keeper/driver,
# backing the 360 view's Vehicles tab. Vehicle itself is tenant-agnostic
# (see app/models/vehicle.py), so the tenant boundary is enforced entirely
# by resolving `customer` through get_customer_or_404 — the vehicle_id a
# caller supplies is looked up with no tenant filter, same as GET
# /v1/vehicles/{id}.


@router.get("/customers/{customer_id}/vehicles", response_model=CustomerVehiclePage)
def list_customer_vehicles(
    customer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    rows = customer_service.list_customer_vehicles(db, customer_id=customer_id)
    return CustomerVehiclePage(items=[CustomerVehicleRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/customers/{customer_id}/vehicles", response_model=CustomerVehicleRead, status_code=201)
def create_customer_vehicle(
    customer_id: uuid.UUID,
    body: CustomerVehicleCreate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    party = customer_service.create_customer_vehicle(db, customer=customer, data=body, actor_id=principal.user_id)
    return CustomerVehicleRead.model_validate(party, from_attributes=True)


@router.patch("/customers/{customer_id}/vehicles/{party_id}", response_model=CustomerVehicleRead)
def update_customer_vehicle(
    customer_id: uuid.UUID,
    party_id: uuid.UUID,
    body: CustomerVehicleUpdate,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    party = customer_service.get_customer_vehicle_or_404(db, customer_id=customer_id, party_id=party_id)
    party = customer_service.update_customer_vehicle(
        db, party=party, data=body, actor_id=principal.user_id, tenant_id=principal.tenant_id
    )
    return CustomerVehicleRead.model_validate(party, from_attributes=True)


@router.delete("/customers/{customer_id}/vehicles/{party_id}", status_code=204)
def delete_customer_vehicle(
    customer_id: uuid.UUID,
    party_id: uuid.UUID,
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    customer_service.get_customer_or_404(db, principal.tenant_id, customer_id)
    party = customer_service.get_customer_vehicle_or_404(db, customer_id=customer_id, party_id=party_id)
    customer_service.delete_customer_vehicle(
        db, party=party, actor_id=principal.user_id, tenant_id=principal.tenant_id
    )
