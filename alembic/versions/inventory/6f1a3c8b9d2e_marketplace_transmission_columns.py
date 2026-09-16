"""Marketplace transmission tracking + StockItem.exterior_colour/body_style (WP-7 PR-9, ADR-062, KAN-27)

`transmission_status`/`last_transmission_error`/`last_attempted_at` are a
SECOND, independent axis from `state` (ADR-054's own pattern): `state` is
the dealer's intent, this is whether the last delivery attempt actually
succeeded. `exterior_colour`/`body_style` close two genuine, previously-
undiscovered gaps — no field anywhere carried either fact for a stock
item, and AS24i's `Aussenfarbe`/`Aufbau` are both mandatory fields
(Schnittstellenbeschrieb v34 p8).

Revision ID: 6f1a3c8b9d2e
Revises: 1945cf1b7a1b
Create Date: 2026-09-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6f1a3c8b9d2e'
down_revision: Union[str, Sequence[str], None] = '1945cf1b7a1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_item_publishing",
        sa.Column("transmission_status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.alter_column("stock_item_publishing", "transmission_status", server_default=None)
    op.add_column("stock_item_publishing", sa.Column("last_transmission_error", sa.Text(), nullable=True))
    op.add_column(
        "stock_item_publishing", sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("stock_item", sa.Column("exterior_colour", sa.String(length=100), nullable=True))
    op.add_column("stock_item", sa.Column("body_style", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_item", "body_style")
    op.drop_column("stock_item", "exterior_colour")
    op.drop_column("stock_item_publishing", "last_attempted_at")
    op.drop_column("stock_item_publishing", "last_transmission_error")
    op.drop_column("stock_item_publishing", "transmission_status")
