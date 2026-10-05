"""sales_contract: rule 2's label for stock_item_id (KAN-144)

KAN-144 lets a manual configuration's contract learn the pipeline stock item
its confirmation created (from `inventory.stock_item.added`'s new
`originContractId` / `originRole`). Writing `stock_item_id` then needs rule 2's
display label and refresh time: `stock_item_label` (the stock number from the
event) and `stock_item_denorm_refreshed_at`, as KAN-150 did for the purchase
replica. Both nullable: a stock-sourced contract's number is never seen by
Sales, and a manual contract stays unlinked until the event arrives.

No backfill: no published event before this change names its contract.

Revision ID: 6d2a8f4c1e95
Revises: 3c9f7e2a5b81
Create Date: 2026-10-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6d2a8f4c1e95'
down_revision: Union[str, Sequence[str], None] = '3c9f7e2a5b81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_contract", sa.Column("stock_item_label", sa.String(length=16), nullable=True))
    op.add_column(
        "sales_contract", sa.Column("stock_item_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sales_contract", "stock_item_denorm_refreshed_at")
    op.drop_column("sales_contract", "stock_item_label")
