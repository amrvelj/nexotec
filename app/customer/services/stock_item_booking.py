"""KAN-632 (ADR-131): what the trade-in consumers know about a stock item's
bookings, from Stock's own events only — never by asking Stock.

Delivery is unordered after a failed delivery (and late under a backlog, an
outage or a deploy), so a purchase or storno event may reach the customer
context after a later booking of the same car. Stock publishes each booking
in its own transaction under the item's row lock, so the events' `occurredAt`
orders the bookings of one item. Two consumers of their own record every
purchase and storno event here (`customer.stock_item_bookings_on_purchased` /
`_on_storno`, registered before the trade-in consumers, so a trade-in step
that fails cannot take its event's row with it; the migration seeded every
one published before, `scripts/backfill_customer_stock_item_booking.py`
re-seeds what a worker without them consumed during the deploy, and the
nightly reconciliation names an event still missing its row). The trade-in
consumers judge an event by what came after it:

- a purchase is overtaken once any later booking of the item is known — a
  later storno reversed it, and a later purchase means it was reversed in
  between (a car is never bought twice without a storno);
- a storno is overtaken once a later purchase is known (KAN-572's stale
  storno: the car was bought again, whether that booking stands or was
  stornoed in turn).

All four consumers take a per-item advisory lock first (`lock_stock_item`),
so a trade-in step and the booking of another event of the same car
serialise: the step either sees that row or commits before it is written —
and then the other event's own trade-in step, which runs after its booking,
sees this one's.
"""

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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


def record_booking(db: Session, *, message: OutboxMessage, kind: StockItemBookingKind) -> bool:
    """This event's row, once: a redelivery, a seeded or backfilled event, or
    a concurrent writer of the same event finds it there (`INSERT … ON
    CONFLICT (event_id) DO NOTHING`, never a unique-violation). Returns
    whether the row was added. Commit-free; the consumer's transaction
    commits it. An event without a tenant is no booking of a Betrieb."""

    if message.tenant_id is None:
        return False
    payload = message.payload or {}
    stock_number = payload.get("stockNumber")
    insert = postgresql_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    added = db.execute(
        insert(StockItemBooking)
        .values(
            event_id=message.id,
            tenant_id=message.tenant_id,
            stock_item_id=message.aggregate_id,
            stock_item_label=str(stock_number)[:64] if stock_number is not None else None,
            stock_item_label_refreshed_at=utcnow(),
            kind=kind,
            occurred_at=message.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(StockItemBooking.event_id)
    ).scalar_one_or_none()
    return added is not None


# The migration's seed (`c4e8a1f7d932`) as a re-seed: every purchase or storno
# event of a Betrieb in the event log without its row. Postgres only (the
# backfill script's lane; `->>` on the JSON payload).
_RESEED_FROM_OUTBOX = """
INSERT INTO customer_stock_item_booking
    (event_id, tenant_id, stock_item_id, stock_item_label, stock_item_label_refreshed_at, kind, occurred_at)
SELECT
    id,
    tenant_id,
    aggregate_id,
    LEFT(payload ->> 'stockNumber', 64),
    occurred_at,
    CASE event_type WHEN 'inventory.stock_item.purchased' THEN 'purchased' ELSE 'stornoed' END,
    occurred_at
FROM outbox_message
WHERE event_type IN ('inventory.stock_item.purchased', 'inventory.stock_item.storno')
  AND tenant_id IS NOT NULL
ON CONFLICT (event_id) DO NOTHING
"""


def reseed_bookings_from_outbox(db: Session) -> int:
    """KAN-632's backfill (`scripts/backfill_customer_stock_item_booking.py`):
    adds the row of every booking event that has none — consumed by a worker
    without the booking consumers during the deploy — and returns how many.
    Idempotent; commit-free."""

    result = db.execute(text(_RESEED_FROM_OUTBOX))
    return int(getattr(result, "rowcount", 0) or 0)


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
