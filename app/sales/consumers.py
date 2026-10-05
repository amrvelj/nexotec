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

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.sales.services.stock_item_purchase import record_stock_item_purchased


def handle_stock_item_purchased_message(db: Session, message: OutboxMessage) -> None:
    """ADR-052 — keeps Sales' local replica of Stock's purchase fact
    (app.sales.services.stock_item_purchase). Flushes only; consume_once
    commits it together with the processed_event row."""

    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.purchased message {message.id} has no tenant_id.")
    record_stock_item_purchased(
        db,
        tenant_id=message.tenant_id,
        stock_item_id=message.aggregate_id,
        event_id=message.id,
        stock_item_label=(message.payload or {}).get("stockNumber"),
        recorded_at=message.occurred_at,
    )
