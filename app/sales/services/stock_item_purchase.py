"""Sales' replica of Stock's purchase fact (ADR-052, KAN-100) — its one
writer (the inventory.stock_item.purchased consumer calls
record_stock_item_purchased) and its one read (stock_item_is_purchased).
Neither ever calls Stock.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract
from app.sales.models.stock_item_purchase import SalesStockItemPurchase


def record_stock_item_purchased(
    db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID, event_id: uuid.UUID
) -> None:
    """ADR-052 — a LOCAL REPLICA of inventory's own is_invoiceable fact,
    never read live from inventory. Stock publishes the event once, so the
    fact is kept per stock item (SalesStockItemPurchase) whether or not a
    contract references the item yet — create_contract reads it for any
    contract written later (KAN-100). Every contract already on the item is
    marked, not just the first one found: a cancelled contract and its
    replacement share a stock item.

    Idempotent by construction (one row per tenant + stock item; setting
    True twice is a no-op), on top of consume_once's processed_event guard.
    Flushes only: consume_once commits this together with its
    processed_event row, so the side effect never outlives a failed
    delivery.
    """

    existing = db.scalar(
        select(SalesStockItemPurchase).where(
            SalesStockItemPurchase.tenant_id == tenant_id, SalesStockItemPurchase.stock_item_id == stock_item_id
        )
    )
    if existing is None:
        db.add(SalesStockItemPurchase(tenant_id=tenant_id, stock_item_id=stock_item_id, source_event_id=event_id))

    db.execute(
        update(SalesContract)
        .where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.stock_item_id == stock_item_id,
            SalesContract.is_invoiceable.is_(False),
        )
        .values(is_invoiceable=True)
        .execution_options(synchronize_session="fetch")
    )
    db.flush()


def stock_item_is_purchased(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> bool:
    """The replica's one read (KAN-100) — local, never a call to Stock."""

    return (
        db.scalar(
            select(SalesStockItemPurchase.id).where(
                SalesStockItemPurchase.tenant_id == tenant_id,
                SalesStockItemPurchase.stock_item_id == stock_item_id,
            )
        )
        is not None
    )
