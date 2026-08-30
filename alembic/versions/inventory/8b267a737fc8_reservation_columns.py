"""Reservation columns on stock_item (WP-7 PR-4, ADR-047)

Revision ID: 8b267a737fc8
Revises: f292ec4d14d8
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8b267a737fc8'
down_revision: Union[str, Sequence[str], None] = 'f292ec4d14d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_item", sa.Column("reserved_by_contract_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("stock_item", sa.Column("active_reservation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "uq_stock_item_active_reservation_id", "stock_item", ["active_reservation_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_stock_item_active_reservation_id", table_name="stock_item")
    op.drop_column("stock_item", "active_reservation_id")
    op.drop_column("stock_item", "reserved_by_contract_id")
