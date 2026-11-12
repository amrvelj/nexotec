"""The customer context's own record of Stock's purchase bookings and stornos (KAN-632, ADR-131)

`customer_stock_item_booking`: one row per `inventory.stock_item.purchased`
and `inventory.stock_item.storno` event, keyed by the event's id, with the
stock item, its stock number (rule 2's label, from the payload) and the
event's `occurred_at`. The trade-in consumers (`customer.trade_in_purchased`,
`customer.trade_in_storno`) judge from it whether the event in front of them
was overtaken by a later booking of the same car — the question they used to
ask Stock (`standing_purchase_date`, `purchase_booked_after`).

Seed: every such event already in `outbox_message` — the event log, never
inventory's table (the KAN-150 precedent, `3c9f7e2a5b81`). The outbox is
never purged, so the projection starts out knowing every booking Stock ever
published, consumed or not; a consumer then inserts its own event's row only
if it is not there yet. One INSERT … SELECT, no rows read in Python, so it
renders under `--sql` too (KAN-174).

Downgrade drops the table; the next upgrade seeds it again from the log.

Revision ID: c4e8a1f7d932
Revises: bd7cbe9376bc
Create Date: 2026-11-12 17:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.core.types

# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f7d932'
down_revision: Union[str, Sequence[str], None] = 'bd7cbe9376bc'
branch_labels: Union[str, Sequence[str], None] = None
# The core chain's outbox_message (the seed reads it), as Sales' 8e3b5d1c7a42 does.
depends_on: Union[str, Sequence[str], None] = ('dec89ed305a2',)


_SEED = """
INSERT INTO customer_stock_item_booking
    (event_id, tenant_id, stock_item_id, stock_item_label, stock_item_label_refreshed_at, kind, occurred_at)
SELECT
    id,
    tenant_id,
    aggregate_id,
    LEFT(payload ->> 'stockNumber', 64),
    occurred_at,
    CASE event_type WHEN 'inventory.stock_item.purchased' THEN 'purchased' ELSE 'stornoed' END,
    occurred_at
FROM outbox_message
WHERE event_type IN ('inventory.stock_item.purchased', 'inventory.stock_item.storno')
  AND tenant_id IS NOT NULL
"""


def upgrade() -> None:
    op.create_table(
        'customer_stock_item_booking',
        sa.Column('event_id', app.core.types.GUID(), nullable=False),
        sa.Column(
            'tenant_id', app.core.types.GUID(), nullable=False,
            comment='Owned by the platform context (Dealership). No DB-level FK.',
        ),
        sa.Column(
            'stock_item_id', app.core.types.GUID(), nullable=False,
            comment='Owned by the inventory context (StockItem). No DB-level FK.',
        ),
        sa.Column('stock_item_label', sa.String(length=64), nullable=True),
        sa.Column('stock_item_label_refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('occurred_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('purchased', 'stornoed')", name='ck_customer_stock_item_booking_kind_enum'),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index(
        'ix_customer_stock_item_booking_item_occurred',
        'customer_stock_item_booking',
        ['tenant_id', 'stock_item_id', 'occurred_at'],
        unique=False,
    )
    op.execute(_SEED)


def downgrade() -> None:
    op.drop_index('ix_customer_stock_item_booking_item_occurred', table_name='customer_stock_item_booking')
    op.drop_table('customer_stock_item_booking')
