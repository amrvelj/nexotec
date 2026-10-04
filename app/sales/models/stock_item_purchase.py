"""SalesStockItemPurchase — Sales' local replica of Stock's purchase fact
(ADR-052, KAN-100).

One row per stock item whose `inventory.stock_item.purchased` event Sales
has consumed, whether or not any contract referenced the item at the time.
The event is published once (inventory.services.stock_item.
mark_purchased_if_ready); keeping the fact keyed by stock item, not on the
contract, is what lets a contract written AFTER the purchase start with
`is_invoiceable = True` without asking Stock synchronously.

Written only by app.sales.consumers (and the KAN-100 migration's backfill
from the outbox). Read by create_contract. SalesContract.is_invoiceable is
derived from this table at creation and on each delivery; this table is the
replica, the contract column its per-contract copy.

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
    # When Sales learned the fact (consumption time), not Stock's booking
    # date — Stock's purchase_date stays Stock's and is not copied here.
    recorded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    source_event_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="outbox_message.id of the inventory.stock_item.purchased event."
    )
