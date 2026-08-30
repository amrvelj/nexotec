"""StockItem endpoints (WP-7 PR-1)."""

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
from app.inventory.models.stock_item import LifecycleStatus, StockItem
from app.inventory.schemas.stock_item import (
    StockItemConditionChange,
    StockItemCreate,
    StockItemPage,
    StockItemRead,
    StockItemUpdate,
)
from app.inventory.services import stock_item as stock_item_service

router = APIRouter(tags=["inventory"])
settings = get_settings()

# U-02/U-03: only indexed columns are offered as sortable — see the
# create_stock_item migration for stock_number/vin/created_at/updated_at.
STOCK_ITEM_SORT_FIELDS: dict[str, object] = {
    "stockNumber": StockItem.stock_number,
    "vin": StockItem.vin,
    "updatedAt": StockItem.updated_at,
    "createdAt": StockItem.created_at,
}
_DEFAULT_STOCK_ITEM_SORT = [
    SortField(api_name="updatedAt", column=StockItem.updated_at, direction="desc", nullable=False)
]


@router.post("/inventory/stock-items", response_model=StockItemRead, status_code=201)
def create_stock_item(
    body: StockItemCreate,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.create_stock_item(db, tenant_id=principal.tenant_id, data=body, actor_id=principal.user_id)
    return StockItemRead.model_validate(item, from_attributes=True)


@router.get("/inventory/stock-items", response_model=StockItemPage)
def list_stock_items(
    q: str | None = None,
    lifecycle_status: LifecycleStatus | None = None,
    sort: str | None = Query(default=None, description="e.g. 'stockNumber:asc,updatedAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=STOCK_ITEM_SORT_FIELDS) or _DEFAULT_STOCK_ITEM_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = stock_item_service.list_stock_items(
        db, tenant_id=principal.tenant_id, q=q, lifecycle_status=lifecycle_status, params=params
    )
    return StockItemPage(
        items=[StockItemRead.model_validate(r, from_attributes=True) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/inventory/stock-items/{stock_item_id}", response_model=StockItemRead)
def get_stock_item(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    return StockItemRead.model_validate(item, from_attributes=True)


@router.patch("/inventory/stock-items/{stock_item_id}", response_model=StockItemRead)
def update_stock_item(
    stock_item_id: uuid.UUID,
    body: StockItemUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.update_stock_item(db, item=item, data=body, actor_id=principal.user_id)
    return StockItemRead.model_validate(item, from_attributes=True)


@router.post("/inventory/stock-items/{stock_item_id}/condition", response_model=StockItemRead)
def change_condition(
    stock_item_id: uuid.UUID,
    body: StockItemConditionChange,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.change_condition(db, item=item, condition=body.condition, actor_id=principal.user_id)
    return StockItemRead.model_validate(item, from_attributes=True)
