"""Odometer, accessories, custody against vehicle_mdm (WP-5 PR-5, ADR-041, amended FR-V-07)

vehicle_mdm_custody_event is a NEW table, not a repoint of the shipped
vehicle_custody_event (which stays untouched, pointed at the old vehicle
table, until PR-7's cutover carries its rows across).

Revision ID: 903b69527f0b
Revises: 285daaedfb85
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '903b69527f0b'
down_revision: Union[str, Sequence[str], None] = '285daaedfb85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_odometer_reading",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("reading_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("implausible", sa.Boolean(), nullable=False),
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
    op.create_index("ix_vehicle_odometer_reading_vehicle_id", "vehicle_odometer_reading", ["vehicle_id"])

    op.create_table(
        "vehicle_accessory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=False),
        sa.Column("accessory_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
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
    op.create_index("ix_vehicle_accessory_vehicle_id", "vehicle_accessory", ["vehicle_id"])

    op.create_table(
        "vehicle_mdm_custody_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=False),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (Dealership). No DB-level FK.",
        ),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the sales context (Transaction). No DB-level FK.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_vehicle_mdm_custody_event_vehicle_id_partner_id",
        "vehicle_mdm_custody_event",
        ["vehicle_id", "partner_id"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_mdm_custody_event")
    op.drop_table("vehicle_accessory")
    op.drop_table("vehicle_odometer_reading")
