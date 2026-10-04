"""sales_stock_item_purchase — Sales' replica of Stock's purchase fact (KAN-100, ADR-052)

Stock publishes `inventory.stock_item.purchased` once. Until now Sales kept
the fact only on a contract that already existed when the event arrived, so
a contract written later on a car already bought stayed not invoiceable
forever. This table keeps the fact per stock item, and
`sales_contract.is_invoiceable` is dropped: SalesContract derives it from
this table in the query that loads the contract, so no stored copy can miss
a purchase consumed while the contract was being written.

`stock_item_id` is inventory's StockItem.id — a plain GUID with the owner
named in its comment, no FK (rule 2). No display label: nothing displays
this row, so the label/refreshed-at half of the three-column pattern has
nothing to carry.

Backfill: every purchase Stock already published is in `outbox_message`
(the outbox is never purged). The backfill reads that event log — not
inventory's stock_item table — keeps the first event per (tenant, stock
item). It writes no outbox rows. Re-runnable: rows already present are
skipped. Contracts need no backfill — their flag is now derived.

Downgrade re-creates the stored column from the replica (true where a row
exists), then drops the table. Not lossless: a legacy sale contract that
scripts/migrate_transaction_rows.py stored as true before KAN-100 comes
back false unless its car has a replica row.

Revision ID: 8e3b5d1c7a42
Revises: 50fe6834bfe2
Create Date: 2026-10-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = '8e3b5d1c7a42'
down_revision: Union[str, Sequence[str], None] = '50fe6834bfe2'
branch_labels: Union[str, Sequence[str], None] = None
# The backfill reads outbox_message, which the core chain creates.
depends_on: Union[str, Sequence[str], None] = ('dec89ed305a2',)

_outbox = sa.table(
    "outbox_message",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("aggregate_id", postgresql.UUID(as_uuid=True)),
    sa.column("event_type", sa.String),
    sa.column("occurred_at", sa.DateTime(timezone=True)),
)
_purchase = sa.table(
    "sales_stock_item_purchase",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("stock_item_id", postgresql.UUID(as_uuid=True)),
    sa.column("recorded_at", sa.DateTime(timezone=True)),
    sa.column("source_event_id", postgresql.UUID(as_uuid=True)),
)
_contract = sa.table(
    "sales_contract",
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("stock_item_id", postgresql.UUID(as_uuid=True)),
    sa.column("is_invoiceable", sa.Boolean),
)


def backfill(conn: sa.Connection) -> None:
    present = {
        (row.tenant_id, row.stock_item_id)
        for row in conn.execute(sa.select(_purchase.c.tenant_id, _purchase.c.stock_item_id))
    }
    events = conn.execute(
        sa.select(_outbox.c.id, _outbox.c.tenant_id, _outbox.c.aggregate_id, _outbox.c.occurred_at)
        .where(_outbox.c.event_type == "inventory.stock_item.purchased", _outbox.c.tenant_id.is_not(None))
        .order_by(_outbox.c.occurred_at, _outbox.c.id)
    )
    rows = []
    for event in events:
        key = (event.tenant_id, event.aggregate_id)
        if key in present:
            continue
        present.add(key)
        rows.append(
            {
                "id": uuid7(),
                "tenant_id": event.tenant_id,
                "stock_item_id": event.aggregate_id,
                "recorded_at": event.occurred_at,
                "source_event_id": event.id,
            }
        )
    if rows:
        conn.execute(sa.insert(_purchase), rows)



def upgrade() -> None:
    op.create_table(
        "sales_stock_item_purchase",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "stock_item_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the inventory context (StockItem.id). No DB-level FK.",
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="outbox_message.id of the inventory.stock_item.purchased event; null for a legacy-migrated purchase.",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "stock_item_id", name="uq_sales_stock_item_purchase_item"),
    )
    op.create_index(
        op.f("ix_sales_stock_item_purchase_tenant_id"), "sales_stock_item_purchase", ["tenant_id"], unique=False
    )
    backfill(op.get_bind())
    op.drop_column("sales_contract", "is_invoiceable")


def downgrade() -> None:
    op.add_column(
        "sales_contract",
        sa.Column("is_invoiceable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("sales_contract", "is_invoiceable", server_default=None)
    purchased = sa.exists().where(
        _purchase.c.tenant_id == _contract.c.tenant_id, _purchase.c.stock_item_id == _contract.c.stock_item_id
    )
    op.get_bind().execute(sa.update(_contract).where(purchased).values(is_invoiceable=True))
    op.drop_index(op.f("ix_sales_stock_item_purchase_tenant_id"), table_name="sales_stock_item_purchase")
    op.drop_table("sales_stock_item_purchase")
