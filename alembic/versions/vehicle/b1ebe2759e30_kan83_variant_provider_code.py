"""KAN-83 — `vehicle_variant_provider_code`, and one forced full reseed.

Resolving a mapping gap never reached the variants it had left `NULL`: the
catalogue sync only ever inserted a variant, and nothing kept the raw code
a field was read from. This table keeps it — one row per (variant,
provider, code_group) — so a resolve fills those variants at once.

Variants synced before this revision have no rows, and the raw codes can
only come from the provider. Clearing every tenant's `last_delta_cursor`
makes the next daily run fall back to a full reseed (the existing,
reported `fell_back_to_full_reseed` path), which records them and fills
whatever the current code map already resolves. Flat-rate billed (ADR-023).

Revision ID: b1ebe2759e30
Revises: e1b483faad5c
Create Date: 2026-10-07 17:41:18.262756

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1ebe2759e30"
down_revision: Union[str, Sequence[str], None] = "e1b483faad5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_variant_provider_code",
        sa.Column("model_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=64), nullable=False),
        sa.Column("code_group", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_variant_id"], ["vehicle_model_variant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_variant_id", "provider", "code_group", name="uq_vehicle_variant_provider_code_field"),
    )
    op.create_index(
        "ix_vehicle_variant_provider_code_code_key",
        "vehicle_variant_provider_code",
        ["provider", "vehicle_kind", "code_group", "provider_code"],
        unique=False,
    )
    op.execute("UPDATE vehicle_provider_sync_state SET last_delta_cursor = NULL")


def downgrade() -> None:
    # The cleared cursors are not restored: the reseed they force is harmless
    # (flat-rate, idempotent), and the old values are gone.
    op.drop_index("ix_vehicle_variant_provider_code_code_key", table_name="vehicle_variant_provider_code")
    op.drop_table("vehicle_variant_provider_code")
