"""Sales' own outbox consumers (WP-8 PR-6) — the first real production
consumer on this side of the codebase (mirrors app.inventory.consumers'
own first-consumer status from WP-7 PR-2).

Registered in app.worker.register_handlers as:

    transport.register(
        "inventory.stock_item.purchased",
        consumer_name="sales.stock_item_purchased",
        handler=handle_stock_item_purchased_message,
    )
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.sales.models.contract import SalesContract


def handle_stock_item_purchased(db: Session, *, tenant_id, stock_item_id) -> None:
    """ADR-052 — a LOCAL REPLICA of inventory's own is_invoiceable fact,
    never read live from inventory at confirmation time. Mirrors exactly
    how WP-7 built the equivalent on Stock's own side (a replicated fact,
    not a synchronous cross-context query). Idempotent by construction:
    setting True twice is a no-op.
    """

    contract = db.scalar(
        select(SalesContract).where(
            SalesContract.tenant_id == tenant_id, SalesContract.stock_item_id == stock_item_id
        )
    )
    if contract is None:
        # No contract references this stock item (yet, or ever) — not an
        # error; most purchased stock items were never sold through a
        # confirmed contract at the time of purchase.
        return
    if contract.is_invoiceable:
        return
    contract.is_invoiceable = True
    db.commit()


def handle_stock_item_purchased_message(db: Session, message: OutboxMessage) -> None:
    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.purchased message {message.id} has no tenant_id.")
    handle_stock_item_purchased(db, tenant_id=message.tenant_id, stock_item_id=message.aggregate_id)
