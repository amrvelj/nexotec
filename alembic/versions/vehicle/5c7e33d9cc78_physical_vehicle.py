"""Physical vehicle: vehicle_mdm + vehicle_number_sequence (WP-5 PR-3, ADR-021, ADR-022, ADR-040)

Creates the new vehicle_mdm table — deliberately separate from the shipped
`vehicle` table, which stays untouched and readable until PR-7's one-way
migration and cutover. Also creates vehicle_number_sequence, a single
global row allocating F-000001 numbers (ADR-022), mirroring
customer_number_sequence's row-lock allocator but global rather than
per-group.

Revision ID: 5c7e33d9cc78
Revises: 3a87612cfe1a
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5c7e33d9cc78'
down_revision: Union[str, Sequence[str], None] = '3a87612cfe1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_number_sequence",
        sa.Column("singleton_key", sa.String(length=16), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )

    op.create_table(
        "vehicle_mdm",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vin", sa.String(length=17), nullable=False, unique=True),
        sa.Column("vehicle_number", sa.String(length=16), nullable=False, unique=True),
        sa.Column("stammnummer", sa.String(length=9), nullable=True, unique=True),
        sa.Column("type_approval_number", sa.String(length=6), nullable=True),
        sa.Column("first_registration_date", sa.Date(), nullable=True),
        sa.Column(
            "catalogue_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_model_variant.id"),
            nullable=True,
        ),
        sa.Column("catalogue_match_status", sa.String(length=16), nullable=False),
        sa.Column("vehicle_status", sa.String(length=16), nullable=False),
        sa.Column(
            "migrated_from_legacy_vehicle_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="PR-7 provenance only — the old vehicle table's id this row was split from, if any.",
        ),
        sa.Column(
            "merged_into_vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_mdm.id"),
            nullable=True,
            comment="PR-6 merge (FR-V-12): set once, on the duplicate, never cleared. One-way, no unmerge.",
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_mdm_vin", "vehicle_mdm", ["vin"])
    op.create_index("ix_vehicle_mdm_vehicle_number", "vehicle_mdm", ["vehicle_number"])
    op.create_index("ix_vehicle_mdm_stammnummer", "vehicle_mdm", ["stammnummer"])
    op.create_index("ix_vehicle_mdm_type_approval_number", "vehicle_mdm", ["type_approval_number"])
    op.create_index(
        "ix_vehicle_mdm_migrated_from_legacy_vehicle_id", "vehicle_mdm", ["migrated_from_legacy_vehicle_id"]
    )
    op.create_index("ix_vehicle_mdm_merged_into_vehicle_id", "vehicle_mdm", ["merged_into_vehicle_id"])


def downgrade() -> None:
    op.drop_table("vehicle_mdm")
    op.drop_table("vehicle_number_sequence")
