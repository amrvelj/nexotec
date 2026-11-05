"""sales_offer: rule 2's stock-number label for its stock car (KAN-157)

A stock-sourced offer — and a contract made from it — copied
`stock_item_id` but never learned the stock number: the offer had no label
column and the contract's (KAN-144) was filled only for a manual
configuration's pipeline item. CLAUDE.md rule 2 asks for the three-column
pattern on every cross-context reference, so the offer gains
`stock_item_label` (the stock number) and `stock_item_denorm_refreshed_at`,
matching `sales_contract`'s pair.

The live path fills both when a stock car is put on an offer or a contract
(the snapshot freeze reads the stock item's pricing, which now carries
`stockNumber` — no new call). Anto (2026-11-03, option A): copied once, never
refreshed — a stock number never changes. Existing offers and contracts are
filled by `scripts/backfill_sales_stock_item_labels.py` (dry run unless
`--commit`): the stock number is Inventory's fact and a migration of this
chain never reads another context's table.

Downgrade drops both columns; they are copies, refilled by the backfill.

Revision ID: 9b4e2d7c1f63
Revises: c5e1a7d3b9f2
Create Date: 2026-11-05 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b4e2d7c1f63"
down_revision: str | Sequence[str] | None = "c5e1a7d3b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales_offer", sa.Column("stock_item_label", sa.String(length=16), nullable=True))
    op.add_column(
        "sales_offer",
        sa.Column("stock_item_denorm_refreshed_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_offer", "stock_item_denorm_refreshed_at")
    op.drop_column("sales_offer", "stock_item_label")
