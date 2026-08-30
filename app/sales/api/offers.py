"""SalesOffer endpoints (WP-8 PR-1, PATCH autosave added PR-2)."""

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
from app.sales.models.offer import SalesOffer
from app.sales.schemas.line_item import LineItemPage, LineItemRead, LineItemsReplaceRequest
from app.sales.schemas.offer import (
    AttachValuationRequest,
    OfferCancelRequest,
    OfferPage,
    OfferRead,
    OfferUpdate,
    TradeInRequest,
)
from app.sales.services import line_items as line_items_service
from app.sales.services import offer as offer_service
from app.sales.services import trade_in as trade_in_service

router = APIRouter(tags=["sales"])
settings = get_settings()

OFFER_SORT_FIELDS: dict[str, object] = {
    "offerNumber": SalesOffer.offer_number,
    "updatedAt": SalesOffer.updated_at,
    "createdAt": SalesOffer.created_at,
}
_DEFAULT_OFFER_SORT = [SortField(api_name="updatedAt", column=SalesOffer.updated_at, direction="desc", nullable=False)]


def _offer_read(offer: SalesOffer) -> OfferRead:
    """containers is computed here (PR-2), never stored — every endpoint
    that returns an OfferRead goes through this one place, matching
    app.inventory's own ageingBucket convention.
    """

    base = OfferRead.model_validate(offer, from_attributes=True)
    return base.model_copy(
        update={
            "containers": offer_service.compute_offer_containers(offer),
            "vehicle_condition": offer_service.vehicle_condition(offer),
        }
    )


@router.post("/sales/offers", response_model=OfferRead, status_code=201)
def create_offer(
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.create_offer(db, tenant_id=principal.tenant_id, actor_id=principal.user_id)
    return _offer_read(offer)


@router.get("/sales/offers", response_model=OfferPage)
def list_offers(
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=OFFER_SORT_FIELDS) or _DEFAULT_OFFER_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = offer_service.list_offers(
        db, tenant_id=principal.tenant_id, params=params
    )
    return OfferPage(
        items=[_offer_read(r) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/sales/offers/{offer_id}", response_model=OfferRead)
def get_offer(
    offer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    return _offer_read(offer)


@router.patch("/sales/offers/{offer_id}", response_model=OfferRead)
def update_offer(
    offer_id: uuid.UUID,
    body: OfferUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = offer_service.update_offer(
        db, offer=offer, group_id=principal.group_id, data=body, actor_id=principal.user_id
    )
    return _offer_read(offer)


@router.post("/sales/offers/{offer_id}/trade-in", response_model=OfferRead)
def set_trade_in(
    offer_id: uuid.UUID,
    body: TradeInRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = trade_in_service.set_trade_in(
        db,
        offer=offer,
        group_id=principal.group_id,
        vin=body.vin,
        plate=body.plate,
        canton=body.canton,
        vehicle_label=body.vehicle_label,
        customer_id=body.customer_id,
        actor_id=principal.user_id,
    )
    return _offer_read(offer)


@router.post("/sales/offers/{offer_id}/trade-in/valuation", response_model=OfferRead)
def attach_trade_in_valuation(
    offer_id: uuid.UUID,
    body: AttachValuationRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = trade_in_service.attach_trade_in_valuation(
        db, offer=offer, valuation_id=body.valuation_id, actor_id=principal.user_id
    )
    return _offer_read(offer)


@router.post("/sales/offers/{offer_id}/finalize", response_model=OfferRead)
def finalize_offer(
    offer_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = offer_service.finalize_offer(db, offer=offer, actor_id=principal.user_id)
    return _offer_read(offer)


@router.post("/sales/offers/{offer_id}/cancel", response_model=OfferRead)
def cancel_offer(
    offer_id: uuid.UUID,
    body: OfferCancelRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = offer_service.cancel_offer(db, offer=offer, reason=body.reason, actor_id=principal.user_id)
    return _offer_read(offer)


@router.get("/sales/offers/{offer_id}/line-items", response_model=LineItemPage)
def list_offer_line_items(
    offer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)  # 404-not-403, tenant scoping
    items = line_items_service.list_line_items(db, tenant_id=principal.tenant_id, offer_id=offer_id)
    return LineItemPage(items=[LineItemRead.model_validate(i, from_attributes=True) for i in items])


@router.put("/sales/offers/{offer_id}/line-items", response_model=OfferRead)
def replace_offer_line_items(
    offer_id: uuid.UUID,
    body: LineItemsReplaceRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    """S-D14 — accessories are a full replace; factory options are a
    per-id patch only (see app.sales.services.line_items's own module
    docstring). Returns the offer itself (recomputed pricing) — the
    caller re-fetches GET .../line-items for the itemised list.
    """

    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
    check_version(offer.version, if_match, entity_name="SalesOffer")
    offer = line_items_service.replace_line_items(db, offer=offer, data=body, actor_id=principal.user_id)
    return _offer_read(offer)
