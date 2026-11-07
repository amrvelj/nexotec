"""sales_deal: the stock car's number on the grid row (KAN-570)

Since KAN-157 an offer and a contract on a stock car carry the car's stock
number (`stock_item_label`, rule 2's label; copied once, never refreshed — a
stock number never changes). The Sales grid reads only this projection, so
it could not show or sort by the number. Anto (2026-11-05, KAN-570 option A):
the deal row carries it too, written by the one projection writer
(`deal_projection.upsert_deal_projection`) from the live entity, compared by
the nightly deal-row StateChecks, and indexed because the grid sorts by it
(U-03: only indexed columns are sortable).

Rows written before are filled by `scripts/backfill_sales_deal_projection.py`
(dry run unless `--commit`), which re-projects every row the deal-row checks
report — a row whose stock number differs from its live entity's is one of
them. The migration copies nothing itself: the backfill goes through the
production writer, the convention of KAN-408.

Downgrade drops the index and the column; it is a copy, refilled by the
backfill.

Revision ID: e7a3c5d1b9f4
Revises: 9b4e2d7c1f63
Create Date: 2026-11-06 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a3c5d1b9f4"
down_revision: str | Sequence[str] | None = "9b4e2d7c1f63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales_deal", sa.Column("stock_item_label", sa.String(length=16), nullable=True))
    op.create_index("ix_sales_deal_stock_item_label", "sales_deal", ["stock_item_label"])


def downgrade() -> None:
    op.drop_index("ix_sales_deal_stock_item_label", table_name="sales_deal")
    op.drop_column("sales_deal", "stock_item_label")
