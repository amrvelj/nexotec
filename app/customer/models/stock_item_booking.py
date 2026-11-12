"""KAN-632: the purchase bookings and stornos of stock items, as this context
has learnt them from Stock's own events — a local projection, so the
trade-in consumers (`consumers.py`, KAN-189 / KAN-572) can tell whether the
event in front of them was overtaken without asking Stock (ADR-131: customer
sits below inventory and never imports it).

One row per `inventory.stock_item.purchased` / `inventory.stock_item.storno`
event, keyed by the event's own id (the outbox message id), carrying when
Stock booked it (the event's `occurredAt`: Stock publishes it in the
booking's own transaction, under the item's row lock, so a later booking of
the same item always carries a later moment). Written by the two consumers
in their own transaction; the customer-chain migration that created the
table filled it from the events already published, so what was consumed
before it is known too. Append-only: a row is never updated or deleted.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class StockItemBookingKind(enum.StrEnum):
    PURCHASED = "purchased"  # inventory.stock_item.purchased
    STORNOED = "stornoed"  # inventory.stock_item.storno


class StockItemBooking(Base):
    __tablename__ = "customer_stock_item_booking"
    __table_args__ = (
        Index("ix_customer_stock_item_booking_item_occurred", "tenant_id", "stock_item_id", "occurred_at"),
    )

    # The event's own id (outbox message id) — not a UUIDv7 minted here:
    # a redelivered or seeded event is the same row.
    event_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership). No DB-level FK."
    )
    # Rule 2's three columns: the stock item's id, its stock number as the
    # event carried it, and when that label was taken (it never changes).
    stock_item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the inventory context (StockItem). No DB-level FK."
    )
    stock_item_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stock_item_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    kind: Mapped[StockItemBookingKind] = mapped_column(StoredEnum(StockItemBookingKind, length=16), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
