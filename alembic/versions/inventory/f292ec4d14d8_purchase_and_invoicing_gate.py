"""Purchase / landed cost / fiktiver Vorsteuerabzug + invoicing gate
columns (WP-7 PR-3/PR-5, ADR-057, ADR-052)

Revision ID: f292ec4d14d8
Revises: 625399ebfc0d
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f292ec4d14d8'
down_revision: Union[str, Sequence[str], None] = '625399ebfc0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_item", sa.Column("supplier_name", sa.String(length=200), nullable=True))
    op.add_column("stock_item", sa.Column("supplier_is_vat_registered", sa.Boolean(), nullable=True))
    op.add_column("stock_item", sa.Column("purchase_date", sa.Date(), nullable=True))
    op.add_column("stock_item", sa.Column("purchase_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("stock_item", sa.Column("purchase_invoice_ref", sa.String(length=120), nullable=True))
    op.add_column("stock_item", sa.Column("landed_cost", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("stock_item", sa.Column("notional_input_tax_applicable", sa.Boolean(), nullable=True))
    op.add_column("stock_item", sa.Column("notional_input_tax_rate", sa.DECIMAL(precision=5, scale=2), nullable=True))
    op.add_column("stock_item", sa.Column("notional_input_tax_amount", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column(
        "stock_item",
        sa.Column("notional_input_tax_overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("stock_item", sa.Column("is_invoiceable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("stock_item", sa.Column("left_stock_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("stock_item", "notional_input_tax_overridden", server_default=None)
    op.alter_column("stock_item", "is_invoiceable", server_default=None)


def downgrade() -> None:
    op.drop_column("stock_item", "left_stock_at")
    op.drop_column("stock_item", "is_invoiceable")
    op.drop_column("stock_item", "notional_input_tax_overridden")
    op.drop_column("stock_item", "notional_input_tax_amount")
    op.drop_column("stock_item", "notional_input_tax_rate")
    op.drop_column("stock_item", "notional_input_tax_applicable")
    op.drop_column("stock_item", "landed_cost")
    op.drop_column("stock_item", "purchase_invoice_ref")
    op.drop_column("stock_item", "purchase_price")
    op.drop_column("stock_item", "purchase_date")
    op.drop_column("stock_item", "supplier_is_vat_registered")
    op.drop_column("stock_item", "supplier_name")
