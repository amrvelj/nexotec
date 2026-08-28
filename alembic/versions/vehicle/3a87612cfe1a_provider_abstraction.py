"""Provider abstraction (WP-5 PR-2, ADR-020)

Creates vehicle_provider_code_map, vehicle_provider_entity_ref and
vehicle_mapping_gap (app.vehicle.models.provider). Fully greenfield — no
provider integration exists yet (WP-6), so these tables start empty; the
migration only establishes the shape mapping_gap's admin queue (PR-8) and
the future gateway sync (WP-6) both need on day one.

Revision ID: 3a87612cfe1a
Revises: 74b6c2794698
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3a87612cfe1a'
down_revision: Union[str, Sequence[str], None] = '74b6c2794698'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_provider_code_map",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=64), nullable=False),
        sa.Column("code_group", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("canonical_list_code", sa.String(length=64), nullable=False),
        sa.Column("canonical_value_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "provider", "vehicle_kind", "code_group", "provider_code",
            name="uq_vehicle_provider_code_map_natural_key",
        ),
    )
    op.create_index("ix_vehicle_provider_code_map_provider", "vehicle_provider_code_map", ["provider"])

    op.create_table(
        "vehicle_provider_entity_ref",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("entity_type", "entity_id", "provider", name="uq_vehicle_provider_entity_ref_natural_key"),
    )
    op.create_index("ix_vehicle_provider_entity_ref_entity_type", "vehicle_provider_entity_ref", ["entity_type"])
    op.create_index("ix_vehicle_provider_entity_ref_entity_id", "vehicle_provider_entity_ref", ["entity_id"])

    op.create_table(
        "vehicle_mapping_gap",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=64), nullable=False),
        sa.Column("code_group", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_value_code", sa.String(length=64), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "provider", "vehicle_kind", "code_group", "provider_code", name="uq_vehicle_mapping_gap_natural_key"
        ),
    )
    op.create_index("ix_vehicle_mapping_gap_provider", "vehicle_mapping_gap", ["provider"])


def downgrade() -> None:
    op.drop_table("vehicle_mapping_gap")
    op.drop_table("vehicle_provider_entity_ref")
    op.drop_table("vehicle_provider_code_map")
