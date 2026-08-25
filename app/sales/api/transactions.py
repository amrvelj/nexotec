"""Transaction endpoints (issue #6): the connective master record linking
Customer + Vehicle + User + Dealership.

Flat endpoints (`/v1/transactions`, not `/v1/dealers/{id}/transactions`),
same shape as Customer — tenant is resolved from the JWT only, Transaction
is always tenant-owned (spec §4 IDs & tenant ownership), no cross-tenant
listing even for platform_admin (same reasoning as Customer: Round 3 scopes
platform_admin's cross-tenant reach to "dealer onboarding only").

/complete is the only path that mutates Vehicle custody/status; /cancel is
a status change only and must never touch the Vehicle (spec §4).
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.audit import list_audit_events
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotency import find_cached_response, store_response
from app.core.pagination import PageParams, page_params
from app.core.permissions import require_read, require_write
from app.db import get_db
from app.platform.public import get_dealership_or_404
from app.sales.models.transaction import TransactionStatus, TransactionType
from app.sales.schemas.transaction import (
    TransactionCancelRequest,
    TransactionCreate,
    TransactionPage,
    TransactionRead,
    TransactionUpdate,
)
from app.sales.services import transaction as transaction_service

router = APIRouter(tags=["transactions"])


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


@router.post("/transactions", response_model=TransactionRead, status_code=201)
def create_transaction(
    body: TransactionCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_write("transactions")),
    db: Session = Depends(get_db),
):
    # Same defensive check as Customer create — tenant is JWT-derived here,
    # no path param to validate against (see module docstring).
    get_dealership_or_404(db, principal.tenant_id)

    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    transaction = transaction_service.create_transaction(
        db, tenant_id=principal.tenant_id, data=body, actor_id=principal.user_id
    )
    result = TransactionRead.model_validate(transaction, from_attributes=True)

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


@router.get("/transactions/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    transaction_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    transaction = transaction_service.get_transaction_or_404(db, principal.tenant_id, transaction_id)
    return TransactionRead.model_validate(transaction, from_attributes=True)


@router.patch("/transactions/{transaction_id}", response_model=TransactionRead)
def update_transaction(
    transaction_id: uuid.UUID,
    body: TransactionUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("transactions")),
    db: Session = Depends(get_db),
):
    transaction = transaction_service.get_transaction_or_404(db, principal.tenant_id, transaction_id)
    check_version(transaction.version, if_match, entity_name="Transaction")
    transaction = transaction_service.update_transaction(
        db, transaction=transaction, data=body, actor_id=principal.user_id
    )
    return TransactionRead.model_validate(transaction, from_attributes=True)


@router.post("/transactions/{transaction_id}/complete", response_model=TransactionRead)
def complete_transaction(
    transaction_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("transactions")),
    db: Session = Depends(get_db),
):
    transaction = transaction_service.get_transaction_or_404(db, principal.tenant_id, transaction_id)
    check_version(transaction.version, if_match, entity_name="Transaction")
    transaction = transaction_service.complete_transaction(db, transaction=transaction, actor_id=principal.user_id)
    return TransactionRead.model_validate(transaction, from_attributes=True)


@router.post("/transactions/{transaction_id}/cancel", response_model=TransactionRead)
def cancel_transaction(
    transaction_id: uuid.UUID,
    body: TransactionCancelRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("transactions")),
    db: Session = Depends(get_db),
):
    transaction = transaction_service.get_transaction_or_404(db, principal.tenant_id, transaction_id)
    check_version(transaction.version, if_match, entity_name="Transaction")
    transaction = transaction_service.cancel_transaction(
        db, transaction=transaction, reason=body.reason, actor_id=principal.user_id
    )
    return TransactionRead.model_validate(transaction, from_attributes=True)


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    customer_id: uuid.UUID | None = None,
    vehicle_id: uuid.UUID | None = None,
    transaction_type: TransactionType | None = None,
    status: TransactionStatus | None = None,
    updated_since: dt.datetime | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows, next_cursor = transaction_service.list_transactions(
        db,
        tenant_id=principal.tenant_id,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        transaction_type=transaction_type,
        status=status,
        updated_since=updated_since,
        params=params,
    )
    return TransactionPage(
        items=[TransactionRead.model_validate(t, from_attributes=True) for t in rows], next_cursor=next_cursor
    )


@router.get("/transactions/{transaction_id}/audit-log", response_model=AuditEventPage)
def get_transaction_audit_log(
    transaction_id: uuid.UUID,
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    transaction_service.get_transaction_or_404(db, principal.tenant_id, transaction_id)
    events = list_audit_events(
        db, entity_type="transaction", entity_id=transaction_id, tenant_id=principal.tenant_id
    )
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in events], next_cursor=None
    )
