"""A trade-in's pipeline item keeps the VIN its contract holds (KAN-536)

`stock_item.trade_in_vin`: written once by inventory's
`sales.contract.confirmed` consumer when it creates a trade-in's pipeline
item, from the event's additive `tradeIn.vin`. «Einkauf buchen» prefills it
and refuses another VIN (422 `trade_in_vin_differs`). It is not `vin`, which
stays empty until the purchase is booked (ADR-045) and is unique among the
Betrieb's live items.

DDL only. Rows written before stay null: the confirmations that created
them carried no VIN, and a migration never reads another context's tables.
Such an item is bought as before, its VIN typed.

Revision ID: 2001f0c37ef5
Revises: 21c14ae9dd31
Create Date: 2026-11-12 22:05:03.876700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2001f0c37ef5'
down_revision: Union[str, Sequence[str], None] = '21c14ae9dd31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_item', sa.Column('trade_in_vin', sa.String(length=17), nullable=True))


def downgrade() -> None:
    op.drop_column('stock_item', 'trade_in_vin')
