"""A trade-in's pipeline item keeps who traded it in (KAN-632, ADR-131)

`stock_item.trade_in_customer_id` and `trade_in_signed_at`: written by
inventory's `sales.contract.confirmed` consumer when it creates a trade-in's
pipeline item, from the event's `customerId` and `signedAt`, and carried on
by the item's purchase and storno events (`tradeInCustomerId`,
`tradeInSignedAt`), so the customer context records the former owner from
the event instead of asking Sales.

DDL only. Rows written before stay null: the confirmations that created
them published neither fact, and a migration never reads another context's
tables. Their events carry neither key, and the worker's fallback asks
Sales for them as before (`app/worker.py`, KAN-632).

Revision ID: 21c14ae9dd31
Revises: c3b63d706c11
Create Date: 2026-11-12 17:09:13.678239

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.core.types

# revision identifiers, used by Alembic.
revision: str = '21c14ae9dd31'
down_revision: Union[str, Sequence[str], None] = 'c3b63d706c11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'stock_item',
        sa.Column(
            'trade_in_customer_id', app.core.types.GUID(), nullable=True,
            comment='Owned by the customer context (Customer). No DB-level FK.',
        ),
    )
    op.add_column('stock_item', sa.Column('trade_in_signed_at', app.core.types.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('stock_item', 'trade_in_signed_at')
    op.drop_column('stock_item', 'trade_in_customer_id')
