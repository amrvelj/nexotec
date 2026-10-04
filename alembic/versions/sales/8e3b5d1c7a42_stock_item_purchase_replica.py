"""sales_stock_item_purchase — Sales' replica of Stock's purchase fact (KAN-100, ADR-052)

Stock publishes `inventory.stock_item.purchased` once. Until now Sales kept
the fact only on a contract that already existed when the event arrived, so
a contract written later on a car already bought stayed not invoiceable
forever. This table keeps the fact per stock item; create_contract reads it.

`stock_item_id` is inventory's StockItem.id — a plain GUID with the owner
named in its comment, no FK (rule 2). No display label: nothing displays
this row, so the label/refreshed-at half of the three-column pattern has
nothing to carry.

Backfill: every purchase Stock already published is in `outbox_message`
(the outbox is never purged). The backfill reads that event log — not
inventory's stock_item table — keeps the first event per (tenant, stock
item), then sets is_invoiceable on the contracts already on those items.
It writes no outbox rows. Re-runnable: rows already present are skipped.

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

    purchased = sa.exists().where(
        _purchase.c.tenant_id == _contract.c.tenant_id, _purchase.c.stock_item_id == _contract.c.stock_item_id
    )
    conn.execute(
        sa.update(_contract).where(_contract.c.is_invoiceable.is_(False), purchased).values(is_invoiceable=True)
    )


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
            nullable=False,
            comment="outbox_message.id of the inventory.stock_item.purchased event.",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "stock_item_id", name="uq_sales_stock_item_purchase_item"),
    )
    op.create_index(
        op.f("ix_sales_stock_item_purchase_tenant_id"), "sales_stock_item_purchase", ["tenant_id"], unique=False
    )
    backfill(op.get_bind())


def downgrade() -> None:
    # sales_contract.is_invoiceable values set by the backfill stay as they
    # are: they are true facts (the purchase was published), and the column
    # predates this revision.
    op.drop_index(op.f("ix_sales_stock_item_purchase_tenant_id"), table_name="sales_stock_item_purchase")
    op.drop_table("sales_stock_item_purchase")
