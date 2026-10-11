"""SalesContract endpoints (WP-8 PR-1; confirmation/invoice-request PR-6;
KAN-67: editing a pending contract — PATCH, trade-in, line items — through
the same services and request bodies as the offer's)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import require_write
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.sales.models.contract import SalesContract
from app.sales.schemas.contract import (
    ContractCancelRequest,
    ContractCreate,
    ContractPage,
    ContractRead,
    ContractUpdate,
)
from app.sales.schemas.line_item import LineItemPage, LineItemRead, LineItemsReplaceRequest
from app.sales.schemas.offer import AttachValuationRequest, TradeInRequest
from app.sales.services import contract as contract_service
from app.sales.services import line_items as line_items_service
from app.sales.services import offer as offer_service
from app.sales.services import trade_in as trade_in_service
from app.sales.services.priced_deal import compute_containers, vehicle_condition

router = APIRouter(tags=["sales"], route_class=IdempotentRoute)
settings = get_settings()

CONTRACT_SORT_FIELDS: dict[str, object] = {
    "contractNumber": SalesContract.contract_number,
    "updatedAt": SalesContract.updated_at,
    "createdAt": SalesContract.created_at,
}
_DEFAULT_CONTRACT_SORT = [
    SortField(api_name="updatedAt", column=SalesContract.updated_at, direction="desc", nullable=False)
]


def _contract_read(contract: SalesContract) -> ContractRead:
    """KAN-67 — containers and vehicle condition are computed here, never
    stored; every endpoint that returns a ContractRead goes through this
    one place (the offer API's _offer_read convention)."""

    base = ContractRead.model_validate(contract, from_attributes=True)
    return base.model_copy(
        update={"containers": compute_containers(contract), "vehicle_condition": vehicle_condition(contract)}
    )


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
        db,
        tenant_id=principal.tenant_id,
        offer=offer,
        actor_id=principal.user_id,
        customer_id=body.customer_id,
        group_id=principal.group_id,
    )
    return _contract_read(contract)


@router.get("/sales/contracts", response_model=ContractPage)
def list_contracts(
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=CONTRACT_SORT_FIELDS) or _DEFAULT_CONTRACT_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = contract_service.list_contracts(
        db, tenant_id=principal.tenant_id, customer_id=customer_id, params=params
    )
    return ContractPage(
        items=[_contract_read(r) for r in rows],
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
    return _contract_read(contract)


@router.patch("/sales/contracts/{contract_id}", response_model=ContractRead)
def update_contract(
    contract_id: uuid.UUID,
    body: ContractUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = contract_service.update_contract(db, contract=contract, data=body, actor_id=principal.user_id)
    return _contract_read(contract)


@router.post("/sales/contracts/{contract_id}/trade-in", response_model=ContractRead)
def set_contract_trade_in(
    contract_id: uuid.UUID,
    body: TradeInRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = trade_in_service.set_trade_in(
        db,
        deal=contract,
        group_id=principal.group_id,
        vin=body.vin,
        plate=body.plate,
        canton=body.canton,
        vehicle_label=body.vehicle_label,
        customer_id=body.customer_id,
        actor_id=principal.user_id,
    )
    return _contract_read(contract)


@router.post("/sales/contracts/{contract_id}/trade-in/valuation", response_model=ContractRead)
def attach_contract_trade_in_valuation(
    contract_id: uuid.UUID,
    body: AttachValuationRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = trade_in_service.attach_trade_in_valuation(
        db, deal=contract, valuation_id=body.valuation_id, actor_id=principal.user_id
    )
    return _contract_read(contract)


@router.get("/sales/contracts/{contract_id}/line-items", response_model=LineItemPage)
def list_contract_line_items(
    contract_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)  # 404-not-403
    items = line_items_service.list_line_items(db, deal=contract)
    return LineItemPage(items=[LineItemRead.model_validate(i, from_attributes=True) for i in items])


@router.put("/sales/contracts/{contract_id}/line-items", response_model=ContractRead)
def replace_contract_line_items(
    contract_id: uuid.UUID,
    body: LineItemsReplaceRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    """S-D14, as on the offer: accessories are a full replace; factory
    options a per-id patch. Returns the contract with its price rebuilt."""

    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
    check_version(contract.version, if_match, entity_name="SalesContract")
    contract = line_items_service.replace_line_items(db, deal=contract, data=body, actor_id=principal.user_id)
    return _contract_read(contract)


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
    return _contract_read(contract)


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
    return _contract_read(contract)


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
    return _contract_read(contract)
