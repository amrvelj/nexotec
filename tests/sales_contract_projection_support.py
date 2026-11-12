"""KAN-630 test support: feed Stock's projection of Sales' contracts the way
the worker does, and record the backfill run.

`feed_projection` delivers every pending `sales.contract.*` fact Stock
consumes to its real consumer through the consumer harness, then marks the
message published — what the worker does once every consumer of it has
succeeded. Other consumers of those events are not run: a test that needs
them delivers them itself.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.inventory.consumers import handle_sales_contract_fact_message
from app.inventory.services.sales_contract import CONSUMERS, record_backfill


def pending_contract_facts(db: Session) -> list[OutboxMessage]:
    return list(
        db.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type.in_(list(CONSUMERS)), OutboxMessage.status == OutboxStatus.PENDING)
            .order_by(OutboxMessage.id)
        ).all()
    )


def deliver_contract_fact(db: Session, message: OutboxMessage) -> None:
    consume_once(
        db, message=message, consumer_name=CONSUMERS[message.event_type], handler=handle_sales_contract_fact_message
    )


def feed_projection(db: Session) -> int:
    """Delivers every pending contract fact; returns how many."""

    messages = pending_contract_facts(db)
    for message in messages:
        deliver_contract_fact(db, message)
        message.status = OutboxStatus.PUBLISHED
        message.published_at = utcnow()
    db.commit()
    return len(messages)


def record_backfill_run(db: Session) -> None:
    record_backfill(db, contracts_read=0, rows_written=0)
    db.commit()
