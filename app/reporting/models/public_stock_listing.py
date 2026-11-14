"""public_stock_listing (KAN-225; Dealer Administration PRD FR-A-27, D-A-26;
ADR-029): what the Betrieb's own website may show of one stock item.

A read model, built only from Stock's
`inventory.stock_item.website_listing_changed` (rule 4/6: the event, consumed
idempotently, never a call into Stock). One row per stock item ever listed;
`is_listed` false keeps the row (and its `revision`) after the car leaves the
website, so a late, older statement cannot put it back.

`listing` holds exactly the fields of `PUBLIC_STOCK_FIELDS`
(`services/public_stock.py`) — never cost, margin, purchase price, valuation
or an internal note: the consumer drops anything else, whatever Stock sends.
Keyed by Betrieb and location, as the website reads it.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class PublicStockListing(PrimaryKeyMixin, Base):
    __tablename__ = "public_stock_listing"
    __table_args__ = (
        UniqueConstraint("stock_item_id", name="uq_public_stock_listing_stock_item_id"),
        Index("ix_public_stock_listing_tenant_location", "tenant_id", "location_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
    stock_item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    is_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    listing: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Stock's statement counter: a statement is applied only when newer.
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
