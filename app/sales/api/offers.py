"""SalesOffer endpoints (WP-8 PR-1)."""

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
from app.sales.schemas.offer import OfferCancelRequest, OfferPage, OfferRead
from app.sales.services import offer as offer_service

router = APIRouter(tags=["sales"])
settings = get_settings()

OFFER_SORT_FIELDS: dict[str, object] = {
    "offerNumber": SalesOffer.offer_number,
    "updatedAt": SalesOffer.updated_at,
    "createdAt": SalesOffer.created_at,
}
_DEFAULT_OFFER_SORT = [SortField(api_name="updatedAt", column=SalesOffer.updated_at, direction="desc", nullable=False)]


@router.post("/sales/offers", response_model=OfferRead, status_code=201)
def create_offer(
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.create_offer(db, tenant_id=principal.tenant_id, actor_id=principal.user_id)
    return OfferRead.model_validate(offer, from_attributes=True)


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
        items=[OfferRead.model_validate(r, from_attributes=True) for r in rows],
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
    return OfferRead.model_validate(offer, from_attributes=True)


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
    return OfferRead.model_validate(offer, from_attributes=True)
