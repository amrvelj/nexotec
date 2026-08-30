"""Factory options + valuation reader endpoints (WP-7 PR-9)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.permissions import require_write
from app.db import get_db
from app.inventory.schemas.pricing import OptionRead, SetOptionsRequest
from app.inventory.schemas.stock_item import StockItemRead
from app.inventory.schemas.valuation import ValuationRefRead
from app.inventory.services import pricing as pricing_service
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services import valuation as valuation_service

router = APIRouter(tags=["inventory"])


@router.get("/inventory/stock-items/{stock_item_id}/options", response_model=list[OptionRead])
def list_options(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    options = pricing_service.list_options(db, tenant_id=principal.tenant_id, stock_item_id=stock_item_id)
    return [OptionRead.model_validate(o, from_attributes=True) for o in options]


@router.put("/inventory/stock-items/{stock_item_id}/options", response_model=StockItemRead)
def set_options(
    stock_item_id: uuid.UUID,
    body: SetOptionsRequest,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    item = pricing_service.set_options(
        db, item=item, base_price=body.base_price, options=body.options, actor_id=principal.user_id
    )
    base = StockItemRead.model_validate(item, from_attributes=True)
    return base.model_copy(update={"ageing_bucket": stock_item_service.compute_ageing_bucket(item)})


@router.get("/inventory/stock-items/{stock_item_id}/valuation", response_model=ValuationRefRead)
def get_valuation(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    return valuation_service.get_valuation_ref(db, tenant_id=principal.tenant_id, stock_item_id=stock_item_id)
