"""Pipeline vehicle columns on stock_item (WP-7 PR-2, ADR-045)

pipeline_ref is the idempotency key for the Sales auto-create paths
(manual configuration / trade-in) — unique per tenant, only when set.
order_date/expected_delivery/in_stock_at back FR-I-14 ageing (PR-7),
which is derived from in_stock_at, never created_at.

Revision ID: 625399ebfc0d
Revises: 9cca34a11074
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '625399ebfc0d'
down_revision: Union[str, Sequence[str], None] = '9cca34a11074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_item", sa.Column("pipeline_ref", sa.String(length=120), nullable=True))
    op.add_column("stock_item", sa.Column("order_date", sa.Date(), nullable=True))
    op.add_column("stock_item", sa.Column("expected_delivery", sa.Date(), nullable=True))
    op.add_column("stock_item", sa.Column("in_stock_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_stock_item_pipeline_ref", "stock_item", ["pipeline_ref"])
    op.create_index(
        "uq_stock_item_tenant_id_pipeline_ref",
        "stock_item",
        ["tenant_id", "pipeline_ref"],
        unique=True,
        postgresql_where=sa.text("pipeline_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_stock_item_tenant_id_pipeline_ref", table_name="stock_item")
    op.drop_index("ix_stock_item_pipeline_ref", table_name="stock_item")
    op.drop_column("stock_item", "in_stock_at")
    op.drop_column("stock_item", "expected_delivery")
    op.drop_column("stock_item", "order_date")
    op.drop_column("stock_item", "pipeline_ref")
