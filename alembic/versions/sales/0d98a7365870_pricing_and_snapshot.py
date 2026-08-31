"""sales_line_item + sales_offer pricing/snapshot columns (WP-8 PR-3,
ADR-041)

Revision ID: 0d98a7365870
Revises: 5ef77f1a4d9c
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0d98a7365870'
down_revision: Union[str, Sequence[str], None] = '5ef77f1a4d9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_offer", sa.Column("vehicle_snapshot", sa.JSON(), nullable=True))
    op.add_column("sales_offer", sa.Column("vehicle_snapshot_frozen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sales_offer", sa.Column("manual_base_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("base_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("options_total", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("list_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("accessories_total", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("total_before_discount", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("discount_type", sa.String(length=16), nullable=True))
    op.add_column("sales_offer", sa.Column("discount_value", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("discount_amount", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("cost_basis", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("margin", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("margin", sa.DECIMAL(precision=12, scale=2), nullable=True))

    op.create_table(
        "sales_line_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("discount_type", sa.String(length=16), nullable=True),
        sa.Column("discount_value", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("discount_resolved_amount", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("discount_suppressed_reason", sa.String(length=200), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sales_line_item_tenant_id", "sales_line_item", ["tenant_id"])
    op.create_index("ix_sales_line_item_offer_id", "sales_line_item", ["offer_id"])
    op.create_index("ix_sales_line_item_contract_id", "sales_line_item", ["contract_id"])


def downgrade() -> None:
    op.drop_table("sales_line_item")
    op.drop_column("sales_contract", "margin")
    op.drop_column("sales_offer", "margin")
    op.drop_column("sales_offer", "cost_basis")
    op.drop_column("sales_offer", "discount_amount")
    op.drop_column("sales_offer", "discount_value")
    op.drop_column("sales_offer", "discount_type")
    op.drop_column("sales_offer", "total_before_discount")
    op.drop_column("sales_offer", "accessories_total")
    op.drop_column("sales_offer", "list_price")
    op.drop_column("sales_offer", "options_total")
    op.drop_column("sales_offer", "base_price")
    op.drop_column("sales_offer", "manual_base_price")
    op.drop_column("sales_offer", "vehicle_snapshot_frozen_at")
    op.drop_column("sales_offer", "vehicle_snapshot")
