"""Plates: vehicle_plate, vehicle_plate_conflict, vehicle_dealer_plate,
vehicle_dealer_plate_assignment (WP-5 PR-4, ADR-039)

vehicle_plate has NO list/browse endpoint anywhere in this codebase and
must never gain one — lookup is by exact (plate, canton) only. See
tests/architecture/test_plate_lookup_is_not_enumerable.py.

Revision ID: 285daaedfb85
Revises: 5c7e33d9cc78
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '285daaedfb85'
down_revision: Union[str, Sequence[str], None] = '5c7e33d9cc78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_plate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=False),
        sa.Column("plate", sa.String(length=16), nullable=False),
        sa.Column("canton", sa.String(length=2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("is_interchangeable", sa.Boolean(), nullable=False),
        sa.Column("plate_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "recording_tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (Dealership). No DB-level FK.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_plate_vehicle_id", "vehicle_plate", ["vehicle_id"])
    # NOT unique on (plate, canton) alone — a Wechselschild is deliberately
    # two rows for the same plate+canton, and plate reassignment over time
    # is deliberately several rows too. Uniqueness is not the guard here;
    # the conflict-detection logic in app.vehicle.services.plate is.
    op.create_index("ix_vehicle_plate_plate_canton", "vehicle_plate", ["plate", "canton"])
    op.create_index("ix_vehicle_plate_plate_group_id", "vehicle_plate", ["plate_group_id"])

    op.create_table(
        "vehicle_plate_conflict",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plate", sa.String(length=16), nullable=False),
        sa.Column("canton", sa.String(length=2), nullable=False),
        sa.Column("first_plate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_plate.id"), nullable=False),
        sa.Column("second_plate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_plate.id"), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_vehicle_plate_conflict_plate", "vehicle_plate_conflict", ["plate"])

    op.create_table(
        "vehicle_dealer_plate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plate", sa.String(length=16), nullable=False),
        sa.Column("canton", sa.String(length=2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_dealer_plate_tenant_id", "vehicle_dealer_plate", ["tenant_id"])

    op.create_table(
        "vehicle_dealer_plate_assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dealer_plate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_dealer_plate.id"), nullable=False
        ),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_vehicle_dealer_plate_assignment_dealer_plate_id", "vehicle_dealer_plate_assignment", ["dealer_plate_id"]
    )
    op.create_index(
        "ix_vehicle_dealer_plate_assignment_vehicle_id", "vehicle_dealer_plate_assignment", ["vehicle_id"]
    )
    op.create_index(
        "ix_vehicle_dealer_plate_assignment_tenant_id", "vehicle_dealer_plate_assignment", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("vehicle_dealer_plate_assignment")
    op.drop_table("vehicle_dealer_plate")
    op.drop_table("vehicle_plate_conflict")
    op.drop_table("vehicle_plate")
