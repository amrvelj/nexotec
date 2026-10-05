"""sales_stock_item_purchase: rule 2's display label (KAN-150)

`stock_item_id` is inventory's StockItem.id. CLAUDE.md rule 2 asks for the
three-column pattern on every cross-context reference: the GUID, a
denormalised display label and when that label was copied. Anto declined an
exception (2026-10-04), so the replica gains `stock_item_label` — the stock
number Stock already publishes on `inventory.stock_item.purchased`
(`payload.stockNumber`), so no event contract change — and
`stock_item_denorm_refreshed_at`, matching `sales_contract`'s
`customer_label` / `customer_denorm_refreshed_at`.

Backfill: each existing row takes the label from its own purchase event in
`outbox_message` (the event log, never inventory's table), refreshed at the
event's `occurred_at`. A row without a source event (a legacy-migrated
purchase) has nothing to read and stays unlabelled; none exist in any
environment, since scripts/migrate_transaction_rows.py has never run with
--commit (KAN-26). Re-runnable: only unlabelled rows are touched.

Downgrade drops both columns; the labels are copies and are rebuilt by the
backfill on the next upgrade.

Revision ID: 3c9f7e2a5b81
Revises: 8e3b5d1c7a42
Create Date: 2026-10-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3c9f7e2a5b81'
down_revision: Union[str, Sequence[str], None] = '8e3b5d1c7a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_outbox = sa.table(
    "outbox_message",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("payload", sa.JSON),
    sa.column("occurred_at", sa.DateTime(timezone=True)),
)
_purchase = sa.table(
    "sales_stock_item_purchase",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("source_event_id", postgresql.UUID(as_uuid=True)),
    sa.column("stock_item_label", sa.String),
    sa.column("stock_item_denorm_refreshed_at", sa.DateTime(timezone=True)),
)


def backfill(conn: sa.Connection) -> None:
    rows = conn.execute(
        sa.select(_purchase.c.id, _outbox.c.payload, _outbox.c.occurred_at)
        .join(_outbox, _outbox.c.id == _purchase.c.source_event_id)
        .where(_purchase.c.stock_item_label.is_(None))
    ).all()
    for row in rows:
        label = (row.payload or {}).get("stockNumber")
        if label is None:
            continue
        conn.execute(
            sa.update(_purchase)
            .where(_purchase.c.id == row.id)
            .values(stock_item_label=label, stock_item_denorm_refreshed_at=row.occurred_at)
        )


def upgrade() -> None:
    op.add_column("sales_stock_item_purchase", sa.Column("stock_item_label", sa.String(length=16), nullable=True))
    op.add_column(
        "sales_stock_item_purchase",
        sa.Column("stock_item_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_column("sales_stock_item_purchase", "stock_item_denorm_refreshed_at")
    op.drop_column("sales_stock_item_purchase", "stock_item_label")
