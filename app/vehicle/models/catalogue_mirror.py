"""The per-tenant catalogue mirror content (WP-6 PR-4) — the actual
colour/tyre-spec/image data a dealer's own auto-i-dat contract delivers
for a variant. The bookkeeping table the full seed and the daily delta wrote
(`vehicle_provider_sync_state`) was dropped with them (KAN-485, KAN-497).

All three tables are tenant-partitioned (ADR-013), same reasoning as
`VariantOption`'s own retrofit in this PR: this is licensed provider
content, cached once per dealer who holds it, never a shared mirror
(there is no such thing — every dealer's auto-i-dat contract is their
own). `ModelVariant` itself (what these rows attach to) stays global —
these tables only hold the provider-delivered specifics one dealer's
contract entitles them to see.
"""

import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID
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
    # KAN-75 — the `colour_area` value (`exterior` | `interior`) the raw
    # `FarbArt` resolves to through the provider code map; NULL while that
    # code is a mapping gap (or the provider sent none). A colour without
    # one is not offered for picking until the gap is resolved (Anto,
    # 2026-10-23, A2). `colour_area_code` is the raw code itself, kept so a
    # resolved gap fills the rows it left NULL at once (B1,
    # `catalogue_sync.apply_resolved_code_to_colours_and_tyres`).
    colour_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    colour_area_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # KAN-43 / FR-C-07 — "the surcharge is a price line in build mode."
    # Nullable/defaulted so a re-sync of an existing row never breaks.
    price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)


class TyreSpecCache(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`PneuDimTS` — one row per (tenant, variant, raw `AchsenCode`, raw
    `PneuTyp`). `season` joined the natural key in KAN-43 (C-E): summer and
    winter specs for the same axle are two different rows, not one row a
    re-sync overwrites. Since KAN-75 the key holds the provider's raw codes,
    not the values they resolve to: an unresolved code has no value yet, and
    two codes an admin maps onto one value stay two rows.
    """

    __tablename__ = "vehicle_tyre_spec_cache"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_variant_id", "axle_code", "season_code",
            name="uq_vehicle_tyre_spec_cache_tenant_variant_raw_codes",
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    # KAN-75 — the `axle_position` value (`front` | `rear` | `both`) the raw
    # `AchsenCode` resolves to through the provider code map; NULL while that
    # code is a mapping gap or the provider sent none. `axle_code` is the raw
    # code (part of the key), kept so a resolved gap fills the row at once.
    axle: Mapped[str | None] = mapped_column(String(16), nullable=True)
    axle_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    load_index: Mapped[str | None] = mapped_column(String(8), nullable=True)
    speed_rating: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # KAN-43 / FR-C-08 — PneuTyp; nullable because not every dimension the
    # provider returns carries one. Since KAN-75 the `tyre_type` value the
    # raw code (`season_code`, part of the key) resolves to; NULL while that
    # code is a gap.
    season: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "summer" | "winter" | "all_season"
    season_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # KAN-43 / FR-C-08 — BemDe: "displayed with the dimension, because a
    # dimension valid only with alloy wheels and shown without that
    # caveat is how the wrong tyre gets ordered."
    remark: Mapped[str | None] = mapped_column(String(160), nullable=True)


class ImageRef(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`Bilder` — a pointer to the provider's own image, never the image
    bytes themselves (no blob storage exists anywhere in this codebase).

    `image_key` (the URL basename) is the sync's own natural/unique key —
    unchanged by KAN-43 (C-E). `image_url` is new: the adapter was
    discarding auto-i-dat's full `BildURL` at ingestion because this
    column didn't exist yet (an explicit WP-6 Open Item); without it there
    was no way for any caller, including the Configurator's own images
    tab, to actually render a photo. Nullable because rows synced before
    this column existed (there were none in any real environment) would
    otherwise need a backfill this ticket has no data to perform.
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
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
