"""Wagenbuch endpoints (WP-7 PR-6)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.permissions import require_write
from app.db import get_db
from app.inventory.schemas.ledger import LedgerEntryPage, LedgerEntryRead, RecordCostRequest
from app.inventory.services import ledger as ledger_service
from app.inventory.services import stock_item as stock_item_service

router = APIRouter(tags=["inventory"])


@router.post("/inventory/stock-items/{stock_item_id}/ledger-entries", response_model=LedgerEntryRead, status_code=201)
def record_cost(
    stock_item_id: uuid.UUID,
    body: RecordCostRequest,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    entry = ledger_service.record_cost(
        db,
        item=item,
        category=body.category,
        amount=body.amount,
        occurred_at=body.occurred_at,
        source_ref=body.source_ref,
        actor_id=principal.user_id,
    )
    return LedgerEntryRead.model_validate(entry, from_attributes=True)


@router.get("/inventory/stock-items/{stock_item_id}/ledger-entries", response_model=LedgerEntryPage)
def list_ledger_entries(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)  # 404s before listing
    entries = ledger_service.list_ledger_entries(db, tenant_id=principal.tenant_id, stock_item_id=stock_item_id)
    return LedgerEntryPage(items=[LedgerEntryRead.model_validate(e, from_attributes=True) for e in entries])
