"""Group-readable stock listing endpoints (WP-7 PR-7, ADR-055).

Two routes for the same underlying read: /groups/mine is what
ScopeSwitchMenu actually calls (the frontend has no reason to ever know
its own raw group id — it's resolved from the JWT here, same "tenant
from the token, never a path/body param" discipline as everything else),
and /groups/{group_id} exists for symmetry / a future admin tool that
genuinely needs to address a specific group.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import ModuleCode, require_entitlement
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.inventory.api.stock_items import STOCK_ITEM_SORT_FIELDS
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.group_listing import (
    GroupStockDealershipList,
    GroupStockDealershipRead,
    StockItemGroupPage,
    StockItemGroupRead,
)
from app.inventory.services.group_listing import list_group_stock_dealerships, list_group_stock_items
from app.platform.public import DealershipRef

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(tags=["inventory"], dependencies=[Depends(require_entitlement(ModuleCode.SALES))])
settings = get_settings()

# KAN-152: the tenant grid's indexed sort columns, and its default order.
# dealershipLabel is deliberately not sortable — it lives on another
# context's table, and ordering by it would mean sorting on a join.
_DEFAULT_GROUP_STOCK_SORT = [
    SortField(api_name="updatedAt", column=StockItem.updated_at, direction="desc", nullable=False)
]


def _group_page_params(
    sort: str | None = Query(default=None, description="e.g. 'stockNumber:asc,updatedAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
) -> SortPageParams:
    return SortPageParams(
        limit=limit,
        cursor=decode_sort_cursor(cursor) if cursor else None,
        sort_fields=parse_sort(sort, allowed=STOCK_ITEM_SORT_FIELDS) or _DEFAULT_GROUP_STOCK_SORT,
    )


# KAN-565 (ADR-055 «the dealership as a filter»): «Betrieb ist» / «ist nicht»
# on the group listing. Repeatable; an id outside the caller's group matches
# nothing (an empty page, never a 403/404 for the id).
_DEALERSHIP_IDS = Query(
    default=None,
    alias="dealershipId",
    description="Only these Betriebe of the group (repeatable). An id outside the group matches nothing.",
)
_DEALERSHIP_IDS_NOT = Query(
    default=None,
    alias="dealershipIdNot",
    description="Every Betrieb of the group but these (repeatable).",
)


def _is_authorized(principal: Principal) -> bool:
    return bool(principal.roles & {AccessRole.INVENTORY, AccessRole.PLATFORM_ADMIN}) or principal.is_dealer_manager


def _to_page(
    rows: list[tuple[StockItem, DealershipRef]], next_cursor: str | None, total: int, total_is_estimate: bool
) -> StockItemGroupPage:
    return StockItemGroupPage(
        items=[
            StockItemGroupRead(
                id=item.id,
                dealership_id=dealership.id,
                dealership_label=dealership.label,
                stock_number=item.stock_number,
                vin=item.vin,
                vehicle_label=item.vehicle_label,
                lifecycle_status=item.lifecycle_status,
                reservation_state=item.reservation_state,
                condition=item.condition,
                odometer_km=item.odometer_km,
                list_price=item.list_price,
                first_registration_date=item.first_registration_date,
                updated_at=item.updated_at,
            )
            for item, dealership in rows
        ],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/inventory/groups/mine/stock-items", response_model=StockItemGroupPage)
def list_my_group_stock(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    q: str | None = None,
    params: SortPageParams = Depends(_group_page_params),
    dealership_ids: list[uuid.UUID] | None = _DEALERSHIP_IDS,
    dealership_ids_not: list[uuid.UUID] | None = _DEALERSHIP_IDS_NOT,
):
    return _to_page(
        *list_group_stock_items(
            db, principal_group_id=principal.group_id, requested_group_id=principal.group_id,
            is_authorized=lambda: _is_authorized(principal), q=q, params=params,
            dealership_ids=dealership_ids, dealership_ids_not=dealership_ids_not,
        )
    )


@router.get("/inventory/groups/mine/dealerships", response_model=GroupStockDealershipList)
def list_my_group_stock_dealerships(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """KAN-565: the Betriebe the group stock listing's «Betrieb» filter
    offers — the same roster and the same gate as the listing (404 for a
    caller it refuses, or while the group's read is off)."""

    refs = list_group_stock_dealerships(
        db, principal_group_id=principal.group_id, requested_group_id=principal.group_id,
        is_authorized=lambda: _is_authorized(principal),
    )
    return GroupStockDealershipList(items=[GroupStockDealershipRead(id=ref.id, label=ref.label) for ref in refs])


@router.get("/inventory/groups/{group_id}/stock-items", response_model=StockItemGroupPage)
def list_group_stock(
    group_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    q: str | None = None,
    params: SortPageParams = Depends(_group_page_params),
    dealership_ids: list[uuid.UUID] | None = _DEALERSHIP_IDS,
    dealership_ids_not: list[uuid.UUID] | None = _DEALERSHIP_IDS_NOT,
):
    return _to_page(
        *list_group_stock_items(
            db, principal_group_id=principal.group_id, requested_group_id=group_id,
            is_authorized=lambda: _is_authorized(principal), q=q, params=params,
            dealership_ids=dealership_ids, dealership_ids_not=dealership_ids_not,
        )
    )
