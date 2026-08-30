"""sales_contract lifecycle/reservation/trade-in/invoicing columns (WP-8
PR-6, ADR-047/ADR-052/S-D18)

Revision ID: 041201c45ee7
Revises: 21cb84f6b528
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '041201c45ee7'
down_revision: Union[str, Sequence[str], None] = '21cb84f6b528'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_contract", sa.Column("vehicle_source", sa.String(length=16), nullable=True))
    op.add_column("sales_contract", sa.Column("manual_vehicle_condition", sa.String(length=16), nullable=True))
    op.add_column(
        "sales_contract",
        sa.Column(
            "trade_in_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK.",
        ),
    )
    op.add_column("sales_contract", sa.Column("trade_in_label", sa.String(length=200), nullable=True))
    op.add_column("sales_contract", sa.Column("trade_in_vin", sa.String(length=17), nullable=True))
    op.add_column(
        "sales_contract",
        sa.Column(
            "trade_in_valuation_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the valuation context (Valuation.id). No DB-level FK.",
        ),
    )
    op.add_column("sales_contract", sa.Column("trade_in_value", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("trade_in_purchase_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("payable", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("financing", sa.String(length=16), nullable=True))
    op.add_column("sales_contract", sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("sales_contract", sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sales_contract", sa.Column("delivery_date", sa.Date(), nullable=True))
    op.add_column(
        "sales_contract", sa.Column("is_invoiceable", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.alter_column("sales_contract", "is_invoiceable", server_default=None)
    op.add_column("sales_contract", sa.Column("invoice_ref", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_contract", "invoice_ref")
    op.drop_column("sales_contract", "is_invoiceable")
    op.drop_column("sales_contract", "delivery_date")
    op.drop_column("sales_contract", "signed_at")
    op.drop_column("sales_contract", "reservation_id")
    op.drop_column("sales_contract", "financing")
    op.drop_column("sales_contract", "payable")
    op.drop_column("sales_contract", "trade_in_purchase_price")
    op.drop_column("sales_contract", "trade_in_value")
    op.drop_column("sales_contract", "trade_in_valuation_id")
    op.drop_column("sales_contract", "trade_in_vin")
    op.drop_column("sales_contract", "trade_in_label")
    op.drop_column("sales_contract", "trade_in_vehicle_id")
    op.drop_column("sales_contract", "manual_vehicle_condition")
    op.drop_column("sales_contract", "vehicle_source")
