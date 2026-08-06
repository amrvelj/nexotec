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
from app.core.pagination import PageParams, page_params
from app.db import get_db
from app.models.customer import CustomerLifecycleStatus
from app.schemas.audit import AuditEventPage, AuditEventRead
from app.schemas.customer import (
    CustomerCreate,
    CustomerDuplicateCandidate,
    CustomerDuplicateCandidateList,
    CustomerMergeRequest,
    CustomerPage,
    CustomerRead,
    CustomerUpdate,
)
from app.services import customer as customer_service
from app.services import dealer as dealer_service
from app.services.audit import list_audit_events
from app.services.idempotency import find_cached_response, store_response

router = APIRouter(tags=["customers"])

_WRITE_ROLES = (AccessRole.DEALER_ADMIN, AccessRole.SALES)


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
    return CustomerDuplicateCandidateList(
        items=[CustomerDuplicateCandidate.model_validate(c, from_attributes=True) for c in rows]
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
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows, next_cursor = customer_service.list_customers(
        db,
        tenant_id=principal.tenant_id,
        q=q,
        lifecycle_status=lifecycle_status,
        updated_since=updated_since,
        params=params,
    )
    return CustomerPage(
        items=[CustomerRead.model_validate(c, from_attributes=True) for c in rows], next_cursor=next_cursor
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
