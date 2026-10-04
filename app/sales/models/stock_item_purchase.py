"""SalesStockItemPurchase — Sales' local replica of Stock's purchase fact
(ADR-052, KAN-100).

One row per stock item whose purchase Sales has learned of — from the
`inventory.stock_item.purchased` event, whether or not any contract
referenced the item at the time.
The event is published once (inventory.services.stock_item.
mark_purchased_if_ready); keeping the fact keyed by stock item, not on the
contract, is what lets a contract written AFTER the purchase start with
`is_invoiceable = True` without asking Stock synchronously.

Written by app.sales.services.stock_item_purchase (from the consumer, and
from scripts/migrate_transaction_rows.py for legacy purchases, which publish
no event) and by the KAN-100 migration's backfill from the outbox.
SalesContract.is_invoiceable is derived from it on read; there is no
per-contract copy to fall out of step.

`inventory.stock_item.storno` (ADR-052: "sets it back") is not emitted by
Stock yet; when it is, its consumer deletes the row and clears the flag.
"""

import datetime as dt
import uuid

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class SalesStockItemPurchase(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "sales_stock_item_purchase"
    __table_args__ = (UniqueConstraint("tenant_id", "stock_item_id", name="uq_sales_stock_item_purchase_item"),)

    stock_item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    # When Stock published the fact (the event's occurred_at); for a legacy
    # purchase, when the migration recorded it. Not Stock's purchase_date,
    # which stays Stock's and is not copied here.
    recorded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        comment="outbox_message.id of the inventory.stock_item.purchased event; null for a legacy-migrated purchase.",
    )
