"""Marketplace publishing (WP-7 PR-8, ADR-062). Three channels — never
more, never generic: AutoScout24 (AS24i v34.0, a file/FTP interface with
full-delivery semantics — an object no longer transmitted is DELETED at
the marketplace, statistics and URL included), Carmarket, Autolina.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class MarketplaceChannel(str, enum.Enum):
    AUTOSCOUT24 = "autoscout24"
    CARMARKET = "carmarket"
    AUTOLINA = "autolina"


class PublishingState(str, enum.Enum):
    NOT_PUBLISHED = "not_published"
    PUBLISHED = "published"


# Per-channel title display limits — the field itself stores up to 500
# characters (zusatztitel), but only this many show on the channel's own
# results list. Not enforced as a DB constraint: it's a live counter the
# UI shows, never a hard validation rule.
TITLE_DISPLAY_LIMITS: dict[MarketplaceChannel, int] = {
    MarketplaceChannel.AUTOSCOUT24: 125,
    MarketplaceChannel.CARMARKET: 80,
    MarketplaceChannel.AUTOLINA: 100,
}

MAX_ADDITIONAL_MEDIA = 15  # + 1 main = 16 total


class StockItemMedia(PrimaryKeyMixin, TenantScopedMixin, Base):
    """Position 1 IS the main image — deliberately no separate `is_main`
    flag (it would drift out of sync with position on reorder)."""

    __tablename__ = "stock_item_media"
    __table_args__ = (
        UniqueConstraint("stock_item_id", "position", name="uq_stock_item_media_stock_item_id_position"),
        # The 1..16 range is enforced in services/publishing.py, not a DB
        # CHECK constraint — reorder_media's own two-phase renumbering
        # (escaping the unique constraint via a temporary out-of-range
        # value) needs positions to be able to pass through values outside
        # 1..16 transiently, which a CHECK constraint (always immediate,
        # never deferrable, on either Postgres or SQLite) would forbid.
    )

    stock_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("stock_item.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # No upload/hosting mechanism built here (same posture as
    # Dealership.logo_url) — an external reference only.
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class StockItemPublishing(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "stock_item_publishing"
    __table_args__ = (
        UniqueConstraint("stock_item_id", "channel", name="uq_stock_item_publishing_stock_item_id_channel"),
    )

    stock_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("stock_item.id"), nullable=False, index=True)
    channel: Mapped[MarketplaceChannel] = mapped_column(SAEnum(MarketplaceChannel, native_enum=False, length=16), nullable=False)
    state: Mapped[PublishingState] = mapped_column(
        SAEnum(PublishingState, native_enum=False, length=16), nullable=False, default=PublishingState.NOT_PUBLISHED
    )
    zusatztitel: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bemerkungen: Mapped[str | None] = mapped_column(Text, nullable=True)
    zustandsbeschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Internal only — "nur für den Betrieb sichtbar," never in the listing.
    haendlerbemerkungen: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_document_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_published_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
