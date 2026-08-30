"""SalesContract endpoints (WP-8 PR-1; confirmation/invoice-request PR-6)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import require_write
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.sales.models.contract import SalesContract
from app.sales.schemas.contract import ContractCancelRequest, ContractCreate, ContractPage, ContractRead
from app.sales.services import contract as contract_service
from app.sales.services import offer as offer_service

router = APIRouter(tags=["sales"])
settings = get_settings()

CONTRACT_SORT_FIELDS: dict[str, object] = {
    "contractNumber": SalesContract.contract_number,
    "updatedAt": SalesContract.updated_at,
    "createdAt": SalesContract.created_at,
}
_DEFAULT_CONTRACT_SORT = [
    SortField(api_name="updatedAt", column=SalesContract.updated_at, direction="desc", nullable=False)
]


@router.post("/sales/contracts", response_model=ContractRead, status_code=201)
def create_contract(
    body: ContractCreate,
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    offer = None
    if body.offer_id is not None:
        offer = offer_service.get_offer_or_404(db, principal.tenant_id, body.offer_id)
    contract = contract_service.create_contract(
        db, tenant_id=principal.tenant_id, offer=offer, actor_id=principal.user_id
    )
    return ContractRead.model_validate(contract, from_attributes=True)


@router.get("/sales/contracts", response_model=ContractPage)
def list_contracts(
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=CONTRACT_SORT_FIELDS) or _DEFAULT_CONTRACT_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = contract_service.list_contracts(
        db, tenant_id=principal.tenant_id, params=params
    )
    return ContractPage(
        items=[ContractRead.model_validate(r, from_attributes=True) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/sales/contracts/{contract_id}", response_model=ContractRead)
def get_contract(
    contract_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    return ContractRead.model_validate(contract, from_attributes=True)


@router.post("/sales/contracts/{contract_id}/confirm", response_model=ContractRead)
def confirm_contract(
    contract_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = contract_service.confirm_contract(
        db, contract=contract, group_id=principal.group_id, actor_id=principal.user_id
    )
    return ContractRead.model_validate(contract, from_attributes=True)


@router.post("/sales/contracts/{contract_id}/request-invoice", response_model=ContractRead)
def request_invoice(
    contract_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = contract_service.request_invoice(db, contract=contract, actor_id=principal.user_id)
    return ContractRead.model_validate(contract, from_attributes=True)


@router.post("/sales/contracts/{contract_id}/cancel", response_model=ContractRead)
def cancel_contract(
    contract_id: uuid.UUID,
    body: ContractCancelRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = contract_service.cancel_contract(db, contract=contract, reason=body.reason, actor_id=principal.user_id)
    return ContractRead.model_validate(contract, from_attributes=True)
