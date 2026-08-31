"""valuation + valuation_deduction + valuation_number_sequence (WP-8 PR-5,
ADR-066/ADR-048 as amended, FR-V-09/FR-V-17)

Revision ID: eb808ea3562c
Revises: e05b17060e01
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'eb808ea3562c'
down_revision: Union[str, Sequence[str], None] = 'e05b17060e01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "valuation_number_sequence",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )

    op.create_table(
        "valuation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valuation_number", sa.String(length=16), nullable=False),
        sa.Column(
            "vehicle_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK.",
        ),
        sa.Column("vehicle_make", sa.String(length=100), nullable=True),
        sa.Column("vehicle_model", sa.String(length=100), nullable=True),
        sa.Column("vehicle_trim", sa.String(length=200), nullable=True),
        sa.Column("vehicle_plate", sa.String(length=16), nullable=True),
        sa.Column("vehicle_vin", sa.String(length=17), nullable=True),
        sa.Column("vehicle_first_registration", sa.Date(), nullable=True),
        sa.Column("mileage", sa.Integer(), nullable=True),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the customer context. No DB-level FK.",
        ),
        sa.Column("customer_label", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("provider_value", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("final_offer", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_valuation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_valuation_tenant_id", "valuation", ["tenant_id"])
    op.create_index("ix_valuation_valuation_number", "valuation", ["valuation_number"])
    op.create_index("ix_valuation_vehicle_id", "valuation", ["vehicle_id"])
    op.create_index("ix_valuation_customer_id", "valuation", ["customer_id"])
    op.create_index("ix_valuation_vehicle_vin", "valuation", ["vehicle_vin"])
    op.create_index("ix_valuation_valid_until", "valuation", ["valid_until"])

    op.create_table(
        "valuation_deduction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valuation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_valuation_deduction_tenant_id", "valuation_deduction", ["tenant_id"])
    op.create_index("ix_valuation_deduction_valuation_id", "valuation_deduction", ["valuation_id"])


def downgrade() -> None:
    op.drop_table("valuation_deduction")
    op.drop_table("valuation")
    op.drop_table("valuation_number_sequence")
