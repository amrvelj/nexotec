"""A stock item on the Betrieb's own website (KAN-225; D-A-26: the
Nexotec-hosted dealer website is the first publishing channel).

Not a fourth marketplace (ADR-062 keeps those to three, each a provider
gateway with full-delivery semantics): the website is Nexotec's own reader
of the public stock projection (FR-A-27), so listing a car there is Stock's
fact, stated on the outbox, and nothing is transmitted anywhere.

`revision` counts the statements Stock has made about this listing — every
`inventory.stock_item.website_listing_changed` carries it, so the projection
applies a statement only when it is newer than the one it holds (delivery is
at-least-once and unordered). `snapshot_digest` is the digest of the public
snapshot last stated, so an edit that changes nothing public states nothing.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class StockItemWebsiteListing(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "stock_item_website_listing"
    __table_args__ = (UniqueConstraint("stock_item_id", name="uq_stock_item_website_listing_stock_item_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
    stock_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("stock_item.id"), nullable=False)
    is_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    listed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    unlisted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
