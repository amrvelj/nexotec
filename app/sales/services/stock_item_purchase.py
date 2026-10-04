"""Sales' replica of Stock's purchase fact (ADR-052, KAN-100) — its writer.
The inventory.stock_item.purchased consumer calls record_stock_item_purchased;
SalesContract.is_invoiceable is derived from the table on read. Never calls
Stock.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.stock_item_purchase import SalesStockItemPurchase


def record_stock_item_purchased(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    event_id: uuid.UUID | None,
    recorded_at: dt.datetime | None = None,
) -> None:
    """Stock publishes the purchase once, so the fact is kept per stock item
    whether or not a contract references the item yet; every contract on
    the item — earlier or later, cancelled or not — derives its
    is_invoiceable from this one row.

    Idempotent by construction (one row per tenant + stock item; the first
    fact wins), on top of consume_once's processed_event guard. Flushes
    only: consume_once commits this together with its processed_event row,
    so the side effect never outlives a failed delivery.

    `event_id=None` only for a purchase recorded without an event — the
    legacy migration (ADR-050 publishes none).
    """

    existing = db.scalar(
        select(SalesStockItemPurchase.id).where(
            SalesStockItemPurchase.tenant_id == tenant_id, SalesStockItemPurchase.stock_item_id == stock_item_id
        )
    )
    if existing is not None:
        return
    row = SalesStockItemPurchase(tenant_id=tenant_id, stock_item_id=stock_item_id, source_event_id=event_id)
    if recorded_at is not None:
        row.recorded_at = recorded_at
    db.add(row)
    db.flush()
