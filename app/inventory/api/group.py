"""Group-readable stock listing endpoints (WP-7 PR-7, ADR-055).

Two routes for the same underlying read: /groups/mine is what
ScopeSwitchMenu actually calls (the frontend has no reason to ever know
its own raw group id — it's resolved from the JWT here, same "tenant
from the token, never a path/body param" discipline as everything else),
and /groups/{group_id} exists for symmetry / a future admin tool that
genuinely needs to address a specific group.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal
from app.db import get_db
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.group_listing import StockItemGroupPage, StockItemGroupRead
from app.inventory.services.group_listing import list_group_stock_items
from app.platform.public import Dealership

router = APIRouter(tags=["inventory"])


def _is_authorized(principal: Principal) -> bool:
    return bool(principal.roles & {AccessRole.INVENTORY, AccessRole.PLATFORM_ADMIN}) or principal.is_dealer_manager


def _to_page(rows: list[tuple[StockItem, Dealership]]) -> StockItemGroupPage:
    return StockItemGroupPage(
        items=[
            StockItemGroupRead(
                id=item.id,
                dealership_id=dealership.id,
                dealership_label=dealership.legal_name,
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
        ]
    )


@router.get("/inventory/groups/mine/stock-items", response_model=StockItemGroupPage)
def list_my_group_stock(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = list_group_stock_items(
        db, principal_group_id=principal.group_id, requested_group_id=principal.group_id,
        is_authorized=lambda: _is_authorized(principal),
    )
    return _to_page(rows)


@router.get("/inventory/groups/{group_id}/stock-items", response_model=StockItemGroupPage)
def list_group_stock(
    group_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = list_group_stock_items(
        db, principal_group_id=principal.group_id, requested_group_id=group_id,
        is_authorized=lambda: _is_authorized(principal),
    )
    return _to_page(rows)
