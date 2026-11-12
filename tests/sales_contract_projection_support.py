"""KAN-630 test support: feed Stock's projection of Sales' contracts the way
the worker does, and record the backfill run.

`feed_projection` delivers every pending `sales.contract.*` fact Stock
consumes to its real consumer through the consumer harness, then marks the
message published — what the worker does once every consumer of it has
succeeded. Other consumers of those events are not run: a test that needs
them delivers them itself.
"""

import contextlib
import re
from collections.abc import Iterator

from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session, sessionmaker

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


# The two reads «projection plus unapplied facts» is made of: Stock's
# projection table (not its backfill record) and the outbox.
_READS_PROJECTION_OR_OUTBOX = re.compile(r"\b(inventory_sales_contract|outbox_message)\b(?!_)", re.IGNORECASE)


@contextlib.contextmanager
def worker_applies_after_the_first_read(engine: Engine) -> Iterator[list[int]]:
    """KAN-630's race, made deterministic: the moment the code under test has
    run its first statement reading the projection or the outbox, a worker on
    a connection of its own applies every pending contract fact and commits
    (projection row and processed-event row together, as the harness does).
    Under READ COMMITTED each later statement of the reader sees that commit.
    Yields a list that holds how many facts the worker applied, once it has."""

    applied: list[int] = []
    worker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def after_execute(conn, cursor, statement, parameters, context, executemany):
        if applied or not statement.lstrip().upper().startswith("SELECT"):
            return
        if not _READS_PROJECTION_OR_OUTBOX.search(statement):
            return
        applied.append(-1)  # guards the worker's own statements against re-entry
        with worker() as w:
            applied[0] = feed_projection(w)

    event.listen(engine, "after_cursor_execute", after_execute)
    try:
        yield applied
    finally:
        event.remove(engine, "after_cursor_execute", after_execute)
