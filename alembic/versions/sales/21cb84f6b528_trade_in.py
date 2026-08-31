"""sales_offer trade-in columns (WP-8 PR-5, S-D18/ADR-064)

Revision ID: 21cb84f6b528
Revises: 0d98a7365870
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '21cb84f6b528'
down_revision: Union[str, Sequence[str], None] = '0d98a7365870'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_offer",
        sa.Column(
            "trade_in_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK.",
        ),
    )
    op.add_column("sales_offer", sa.Column("trade_in_label", sa.String(length=200), nullable=True))
    op.add_column("sales_offer", sa.Column("trade_in_vin", sa.String(length=17), nullable=True))
    op.add_column(
        "sales_offer",
        sa.Column(
            "trade_in_valuation_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the valuation context (Valuation.id). No DB-level FK.",
        ),
    )
    op.add_column("sales_offer", sa.Column("trade_in_value", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("trade_in_purchase_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("payable", sa.DECIMAL(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_offer", "payable")
    op.drop_column("sales_offer", "trade_in_purchase_price")
    op.drop_column("sales_offer", "trade_in_value")
    op.drop_column("sales_offer", "trade_in_valuation_id")
    op.drop_column("sales_offer", "trade_in_vin")
    op.drop_column("sales_offer", "trade_in_label")
    op.drop_column("sales_offer", "trade_in_vehicle_id")
