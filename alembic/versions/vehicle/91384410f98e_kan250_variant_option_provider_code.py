"""KAN-250 — `vehicle_variant_option_provider_code`, and one forced full reseed.

Resolving an `equipment_feature` mapping gap never reached the options it
had been blocking: the catalogue sync skips an unresolved `SuchCode` and
nothing kept an option's raw code list. This table keeps it — one row per
(option, provider, code_group, provider_code), tenant-partitioned like the
option (ADR-013) — so a resolve adds the feature to those options at once.
Same shape as `vehicle_variant_provider_code` (KAN-83) plus `tenant_id`.

Options synced before this revision have no rows, and the raw codes can only
come from the provider. Clearing every tenant's `last_delta_cursor` makes
the next daily run fall back to a full reseed (the existing, reported
`fell_back_to_full_reseed` path — KAN-83's precedent), which records them.
Flat-rate billed (ADR-023).

Revision ID: 91384410f98e
Revises: ff5b97c11665
Create Date: 2026-10-12 11:52:24.291389

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91384410f98e"
down_revision: str | Sequence[str] | None = "ff5b97c11665"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_variant_option_provider_code",
        sa.Column("variant_option_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=64), nullable=False),
        sa.Column("code_group", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["variant_option_id"], ["vehicle_variant_option.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "variant_option_id", "provider", "code_group", "provider_code",
            name="uq_vehicle_variant_option_provider_code_code",
        ),
    )
    op.create_index(
        "ix_vehicle_variant_option_provider_code_code_key",
        "vehicle_variant_option_provider_code",
        ["provider", "vehicle_kind", "code_group", "provider_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vehicle_variant_option_provider_code_tenant_id"),
        "vehicle_variant_option_provider_code",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vehicle_variant_option_provider_code_variant_option_id"),
        "vehicle_variant_option_provider_code",
        ["variant_option_id"],
        unique=False,
    )
    op.execute("UPDATE vehicle_provider_sync_state SET last_delta_cursor = NULL")


def downgrade() -> None:
    # The cleared cursors are not restored: the reseed they force is harmless
    # (flat-rate, idempotent), and the old values are gone.
    op.drop_index(
        op.f("ix_vehicle_variant_option_provider_code_variant_option_id"),
        table_name="vehicle_variant_option_provider_code",
    )
    op.drop_index(
        op.f("ix_vehicle_variant_option_provider_code_tenant_id"), table_name="vehicle_variant_option_provider_code"
    )
    op.drop_index(
        "ix_vehicle_variant_option_provider_code_code_key", table_name="vehicle_variant_option_provider_code"
    )
    op.drop_table("vehicle_variant_option_provider_code")
