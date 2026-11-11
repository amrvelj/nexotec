"""KAN-553 (ADR-115): «Fahrzeug im Lager geändert» on open offers and pending contracts

Stock publishes `inventory.stock_item.commercial_changed` when a car's price,
options, colour, odometer or configuration label changes; Sales' consumer
flags every draft or open offer and every pending contract on that car:
`vehicle_changed_at` (when Stock last changed it) and `vehicle_changed_fields`
(the changed field names, merged). Refresh or dismiss clears both;
dismiss stamps `vehicle_change_dismissed_at`, so the nightly check does not
report the price the seller chose to keep. The deal
grid carries `vehicle_changed_at` for its filter and marker. Nullable, no
backfill: nothing was flagged before.

Revision ID: c4f8a2d6e1b3
Revises: e7a3c5d1b9f4
Create Date: 2026-11-11 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f8a2d6e1b3"
down_revision: str | Sequence[str] | None = "e7a3c5d1b9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("sales_offer", "sales_contract"):
        op.add_column(table, sa.Column("vehicle_changed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("vehicle_changed_fields", sa.JSON(), nullable=True))
        op.add_column(table, sa.Column("vehicle_change_dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sales_deal", sa.Column("vehicle_changed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_deal", "vehicle_changed_at")
    for table in ("sales_contract", "sales_offer"):
        op.drop_column(table, "vehicle_change_dismissed_at")
        op.drop_column(table, "vehicle_changed_fields")
        op.drop_column(table, "vehicle_changed_at")
