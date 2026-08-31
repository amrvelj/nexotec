"""The per-tenant catalogue mirror content (WP-6 PR-4) — the actual
colour/tyre-spec/image data a dealer's own auto-i-dat contract delivers
for a variant, plus the bookkeeping `ProviderSyncState` the daily
seed/delta job and the sync-age alarm both read and write.

All four tables are tenant-partitioned (ADR-013), same reasoning as
`VariantOption`'s own retrofit in this PR: this is licensed provider
content, cached once per dealer who holds it, never a shared mirror
(there is no such thing — every dealer's auto-i-dat contract is their
own). `ModelVariant` itself (what these rows attach to) stays global —
these tables only hold the provider-delivered specifics one dealer's
contract entitles them to see.
"""

import datetime as dt
import uuid

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ColourCache(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`OptionenFarben` — one row per (tenant, variant, colour_code)."""

    __tablename__ = "vehicle_colour_cache"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_variant_id", "colour_code", name="uq_vehicle_colour_cache_tenant_variant_code"
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    colour_code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    colour_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "exterior" | "interior"


class TyreSpecCache(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`PneuDimTS` — one row per (tenant, variant, axle)."""

    __tablename__ = "vehicle_tyre_spec_cache"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_variant_id", "axle", name="uq_vehicle_tyre_spec_cache_tenant_variant_axle"
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    axle: Mapped[str] = mapped_column(String(16), nullable=False)  # "front" | "rear"
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    load_index: Mapped[str | None] = mapped_column(String(8), nullable=True)
    speed_rating: Mapped[str | None] = mapped_column(String(4), nullable=True)


class ImageRef(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`Bilder` — a pointer to the provider's own image key, never the
    image bytes themselves (no blob storage exists anywhere in this
    codebase, per the WP-6 plan's own Open Items — this table is the seam
    a future image-fetch/cache step would read, not that step itself).
    """

    __tablename__ = "vehicle_image_ref"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_variant_id", "image_key", name="uq_vehicle_image_ref_tenant_variant_key"
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    bild_typ: Mapped[str] = mapped_column(String(16), nullable=False)
    bild_art: Mapped[str] = mapped_column(String(16), nullable=False)
    image_key: Mapped[str] = mapped_column(String(160), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProviderSyncState(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One row per (tenant, provider) — what `catalogue_sync.py` and the
    sync-age alarm (A-12) both read and write. `last_system_watermark_date`
    is the provider's own `System.StandDatum`, fetched fresh at alarm-check
    time (not just carried over from the last successful delta) — A-12's
    whole point is that a job reporting success proves nothing about
    whether the *provider's own* data is still moving forward.
    """

    __tablename__ = "vehicle_provider_sync_state"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_code", name="uq_vehicle_provider_sync_state_tenant_provider"),
    )

    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    last_full_seed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_delta_cursor: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_system_watermark_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_system_checked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
