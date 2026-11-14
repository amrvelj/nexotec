"""Vehicle's projection of the vehicle-mdm records a Betrieb holds in stock
(KAN-667).

ADR-131 puts Vehicle below Inventory: Vehicle never asks Stock. What the
identity PATCH needs to know — does the caller's Betrieb hold this record in
stock, so a VIN change must go through «VIN korrigieren» (KAN-557) instead? —
Vehicle keeps here, one row per stock item, fed by Stock's own facts through
the outbox (`app.vehicle.services.stock_holding`):

- `inventory.pipeline_vehicle.vin_assigned` — promotion gave the item its
  vehicle-mdm record (`vehicleId`). An item's record is set once and kept
  through «Einkauf stornieren» (the stornoed car keeps its VIN and record and
  still binds them, KAN-146) and «VIN korrigieren» (the same record, its VIN
  rewritten): from here on the item holds the record.
- `inventory.stock_item.sold` — the item left stock (`left_stock_at`); a car
  that comes back is a new item. Final.

and by `scripts/backfill_vehicle_stock_holding.py` for items older than the
consumers. Written only by Vehicle; Stock's tables are never read here (the
nightly reconciliation compares the two, rule 10).

Delivery is at-least-once and unordered, so every write is a merge that gives
the same row whatever order the facts arrive in: `vehicle_id`, the stock
number and `left_stock_at` are each set once and never cleared. One writer
moves `vehicle_id`: a vehicle merge re-points the duplicate's rows onto the
survivor (KAN-524, `services/merge.py`), as it does plates; a fact applied
after the merge is stored on the live record.
"""

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class VehicleStockHolding(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "vehicle_stock_holding"
    __table_args__ = (
        UniqueConstraint("tenant_id", "stock_item_id", name="uq_vehicle_stock_holding_tenant_id_stock_item_id"),
        # The PATCH's and the detail read's question: does this Betrieb hold
        # this record in stock?
        Index("ix_vehicle_stock_holding_vehicle_id_tenant_id", "vehicle_id", "tenant_id"),
    )

    stock_item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    # Rule 2's display label (the stock number, never re-allocated) and when
    # it was read. Null when the fact that created the row carried none (a
    # `sold` fact delivered first, or a `vin_assigned` published before
    # KAN-667 added the number).
    stock_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stock_number_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # The record the item holds; null until its `vin_assigned` arrives (a
    # `sold` fact may come first).
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=True)
    # When the item left stock (the `sold` fact's occurred_at); a row with it
    # set holds nothing.
    left_stock_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
