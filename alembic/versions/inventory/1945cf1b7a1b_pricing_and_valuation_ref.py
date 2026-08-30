"""base_price + valuation_ref_* + stock_item_option (WP-7 PR-9, FR-I-22,
ADR-066/ADR-048)

Revision ID: 1945cf1b7a1b
Revises: 11d10e3e0abf
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1945cf1b7a1b'
down_revision: Union[str, Sequence[str], None] = '11d10e3e0abf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_item", sa.Column("base_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("stock_item", sa.Column("valuation_ref_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("stock_item", sa.Column("valuation_ref_amount", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("stock_item", sa.Column("valuation_ref_valued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stock_item", sa.Column("valuation_ref_source", sa.String(length=120), nullable=True))

    op.create_table(
        "stock_item_option",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column("stock_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_item.id"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("price", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("equipment_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stock_item_option_tenant_id", "stock_item_option", ["tenant_id"])
    op.create_index("ix_stock_item_option_stock_item_id", "stock_item_option", ["stock_item_id"])


def downgrade() -> None:
    op.drop_table("stock_item_option")
    op.drop_column("stock_item", "valuation_ref_source")
    op.drop_column("stock_item", "valuation_ref_valued_at")
    op.drop_column("stock_item", "valuation_ref_amount")
    op.drop_column("stock_item", "valuation_ref_id")
    op.drop_column("stock_item", "base_price")
