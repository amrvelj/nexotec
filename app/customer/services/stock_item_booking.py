"""KAN-632 (ADR-131): what the trade-in consumers know about a stock item's
bookings, from Stock's own events only — never by asking Stock.

Delivery is unordered after a failed delivery (and late under a backlog, an
outage or a deploy), so a purchase or storno event may reach the customer
context after a later booking of the same car. Stock publishes each booking
in its own transaction under the item's row lock, so the events' `occurredAt`
orders the bookings of one item. The consumers record every purchase and
storno event they see here (`models/stock_item_booking.py`; the migration
seeded every one published before) and judge an event by what came after it:

- a purchase is overtaken once any later booking of the item is known — a
  later storno reversed it, and a later purchase means it was reversed in
  between (a car is never bought twice without a storno);
- a storno is overtaken once a later purchase is known (KAN-572's stale
  storno: the car was bought again, whether that booking stands or was
  stornoed in turn).

Both consumers take a per-item advisory lock first (`lock_stock_item`), so a
purchase and a storno of the same car consumed at the same time see each
other's row in the order they commit.
"""

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.customer.models.stock_item_booking import StockItemBooking, StockItemBookingKind

_LOCK_NAMESPACE = b"customer.stock_item_booking:"


def lock_stock_item(db: Session, *, stock_item_id: uuid.UUID) -> None:
    """pg_advisory_xact_lock on a 64-bit key derived from the stock item;
    released by the consumer transaction's commit or rollback. A no-op on
    SQLite (the fast local lane, ADR-011), which serialises writers itself."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.blake2b(_LOCK_NAMESPACE + stock_item_id.bytes, digest_size=8).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest, "big", signed=True)})


def record_booking(db: Session, *, message: OutboxMessage, kind: StockItemBookingKind) -> None:
    """This event's row, once: a redelivery, or an event the migration seeded,
    finds it there. Flushes only; the consumer's transaction commits it."""

    if message.tenant_id is None or db.get(StockItemBooking, message.id) is not None:
        return
    payload = message.payload or {}
    stock_number = payload.get("stockNumber")
    db.add(
        StockItemBooking(
            event_id=message.id,
            tenant_id=message.tenant_id,
            stock_item_id=message.aggregate_id,
            stock_item_label=str(stock_number)[:64] if stock_number is not None else None,
            stock_item_label_refreshed_at=utcnow(),
            kind=kind,
            occurred_at=message.occurred_at,
        )
    )
    db.flush()


def _booked_after(
    db: Session, *, message: OutboxMessage, kinds: tuple[StockItemBookingKind, ...]
) -> bool:
    return (
        db.scalar(
            select(StockItemBooking.event_id)
            .where(
                StockItemBooking.tenant_id == message.tenant_id,
                StockItemBooking.stock_item_id == message.aggregate_id,
                StockItemBooking.kind.in_(kinds),
                StockItemBooking.occurred_at > message.occurred_at,
                StockItemBooking.event_id != message.id,
            )
            .limit(1)
        )
        is not None
    )


def purchase_overtaken(db: Session, *, message: OutboxMessage) -> bool:
    """Whether a later storno or purchase of the item is known: the purchase
    this event reports no longer stands."""

    return _booked_after(db, message=message, kinds=(StockItemBookingKind.STORNOED, StockItemBookingKind.PURCHASED))


def storno_overtaken(db: Session, *, message: OutboxMessage) -> bool:
    """Whether a later purchase of the item is known: the storno is stale and
    says nothing about who holds the car now."""

    return _booked_after(db, message=message, kinds=(StockItemBookingKind.PURCHASED,))
