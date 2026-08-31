"""Catalogue mirror + VariantOption tenant-partitioning retrofit (WP-6 PR-4)

ADR-013 — licensed provider data is tenant-partitioned, never global.
`vehicle_variant_option` existed since WP-5 PR-1 with no `tenant_id` — a
real gap against that rule, closed here rather than left for a later PR,
since the table was empty in every environment (no seed/production data
references it — confirmed directly, not assumed) and needed no backfill.
`price` is added alongside `tenant_id` since PR-4's catalogue_sync now
persists it (VariantOptionData.price from the provider-gateway's own
shape); it had no column before this PR simply because nothing wrote to
this table until now.

Four new tables, all tenant-scoped: `vehicle_colour_cache`/
`vehicle_tyre_spec_cache`/`vehicle_image_ref` hold the actual per-tenant
mirrored content (OptionenFarben/PneuDimTS/Bilder); `vehicle_provider_
sync_state` is the bookkeeping the daily seed/delta job and the A-12
sync-age alarm both read and write, one row per (tenant, provider_code).

Revision ID: b2e6f1a9c3d7
Revises: 1428f5f37b66
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2e6f1a9c3d7'
down_revision: Union[str, Sequence[str], None] = '1428f5f37b66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vehicle_variant_option",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.add_column("vehicle_variant_option", sa.Column("price", sa.DECIMAL(12, 2), nullable=True))
    op.create_index("ix_vehicle_variant_option_tenant_id", "vehicle_variant_option", ["tenant_id"])
    op.create_unique_constraint(
        "uq_vehicle_variant_option_tenant_variant_code",
        "vehicle_variant_option",
        ["tenant_id", "model_variant_id", "option_code"],
    )

    op.create_table(
        "vehicle_colour_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"), nullable=False
        ),
        sa.Column("colour_code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=160), nullable=False),
        sa.Column("colour_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_colour_cache_tenant_id", "vehicle_colour_cache", ["tenant_id"])
    op.create_index("ix_vehicle_colour_cache_model_variant_id", "vehicle_colour_cache", ["model_variant_id"])
    op.create_unique_constraint(
        "uq_vehicle_colour_cache_tenant_variant_code",
        "vehicle_colour_cache",
        ["tenant_id", "model_variant_id", "colour_code"],
    )

    op.create_table(
        "vehicle_tyre_spec_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"), nullable=False
        ),
        sa.Column("axle", sa.String(length=16), nullable=False),
        sa.Column("size", sa.String(length=32), nullable=False),
        sa.Column("load_index", sa.String(length=8), nullable=True),
        sa.Column("speed_rating", sa.String(length=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_tyre_spec_cache_tenant_id", "vehicle_tyre_spec_cache", ["tenant_id"])
    op.create_index("ix_vehicle_tyre_spec_cache_model_variant_id", "vehicle_tyre_spec_cache", ["model_variant_id"])
    op.create_unique_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_axle",
        "vehicle_tyre_spec_cache",
        ["tenant_id", "model_variant_id", "axle"],
    )

    op.create_table(
        "vehicle_image_ref",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"), nullable=False
        ),
        sa.Column("bild_typ", sa.String(length=16), nullable=False),
        sa.Column("bild_art", sa.String(length=16), nullable=False),
        sa.Column("image_key", sa.String(length=160), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_image_ref_tenant_id", "vehicle_image_ref", ["tenant_id"])
    op.create_index("ix_vehicle_image_ref_model_variant_id", "vehicle_image_ref", ["model_variant_id"])
    op.create_unique_constraint(
        "uq_vehicle_image_ref_tenant_variant_key", "vehicle_image_ref", ["tenant_id", "model_variant_id", "image_key"]
    )

    op.create_table(
        "vehicle_provider_sync_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("last_full_seed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delta_cursor", sa.Date(), nullable=True),
        sa.Column("last_system_watermark_date", sa.Date(), nullable=True),
        sa.Column("last_system_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_provider_sync_state_tenant_id", "vehicle_provider_sync_state", ["tenant_id"])
    op.create_unique_constraint(
        "uq_vehicle_provider_sync_state_tenant_provider",
        "vehicle_provider_sync_state",
        ["tenant_id", "provider_code"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_provider_sync_state")
    op.drop_table("vehicle_image_ref")
    op.drop_table("vehicle_tyre_spec_cache")
    op.drop_table("vehicle_colour_cache")
    op.drop_constraint("uq_vehicle_variant_option_tenant_variant_code", "vehicle_variant_option", type_="unique")
    op.drop_index("ix_vehicle_variant_option_tenant_id", table_name="vehicle_variant_option")
    op.drop_column("vehicle_variant_option", "price")
    op.drop_column("vehicle_variant_option", "tenant_id")
