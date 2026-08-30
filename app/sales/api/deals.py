"""Overview grid endpoint (WP-8 PR-1). Read-only — nothing writes a deal
directly, ever; every mutation flows through offers.py/contracts.py and
lands here via upsert_deal_projection.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.sales.models.deal import SalesDeal
from app.sales.schemas.deal import DealPage, DealRead
from app.sales.services import deal as deal_service

router = APIRouter(tags=["sales"])
settings = get_settings()

# U-03: only indexed columns are sortable — see the sales_offer_contract_deal
# migration for number/customer_label/vehicle_label/gross_price/updated_at.
DEAL_SORT_FIELDS: dict[str, object] = {
    "number": SalesDeal.number,
    "customerLabel": SalesDeal.customer_label,
    "vehicleLabel": SalesDeal.vehicle_label,
    "grossPrice": SalesDeal.gross_price,
    "updatedAt": SalesDeal.updated_at,
    "createdAt": SalesDeal.created_at,
}
_DEFAULT_DEAL_SORT = [SortField(api_name="updatedAt", column=SalesDeal.updated_at, direction="desc", nullable=False)]


@router.get("/sales/deals", response_model=DealPage)
def list_deals(
    q: str | None = None,
    entity_type: str | None = Query(default=None, alias="entityType"),
    sort: str | None = Query(default=None, description="e.g. 'updatedAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=DEAL_SORT_FIELDS) or _DEFAULT_DEAL_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = deal_service.list_deals(
        db, tenant_id=principal.tenant_id, q=q, entity_type=entity_type, params=params
    )
    return DealPage(
        items=[DealRead.model_validate(r, from_attributes=True) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )
