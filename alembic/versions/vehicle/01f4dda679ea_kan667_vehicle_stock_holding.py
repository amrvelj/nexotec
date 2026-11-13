"""vehicle_stock_holding — Vehicle's projection of the records a Betrieb holds in stock (KAN-667)

ADR-131 puts Vehicle below Inventory, so the identity PATCH cannot ask Stock
whether a Betrieb holds a record in stock before it changes the VIN. It reads
this projection instead, fed by `inventory.pipeline_vehicle.vin_assigned`
(the item got its record) and `inventory.stock_item.sold` (it left stock)
through the outbox. `stock_item_id` is Stock's id (three-column pattern: the
stock number is its label); `vehicle_id` names this context's own record.
Schema only: an empty table, no data moved and no other context's table read
(`scripts/backfill_vehicle_stock_holding.py` fills it for cars already in
stock), so it renders under `--sql` too.

Revision ID: 01f4dda679ea
Revises: 8d03887f7876
Create Date: 2026-11-13 17:48:17.077235

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "01f4dda679ea"
down_revision: Union[str, Sequence[str], None] = "8d03887f7876"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_stock_holding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "stock_item_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the inventory context (StockItem.id). No DB-level FK.",
        ),
        sa.Column("stock_number", sa.String(length=16), nullable=True),
        sa.Column("stock_number_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_mdm.id"), nullable=True),
        sa.Column("left_stock_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "stock_item_id", name="uq_vehicle_stock_holding_tenant_id_stock_item_id"),
    )
    op.create_index("ix_vehicle_stock_holding_tenant_id", "vehicle_stock_holding", ["tenant_id"])
    op.create_index(
        "ix_vehicle_stock_holding_vehicle_id_tenant_id", "vehicle_stock_holding", ["vehicle_id", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_vehicle_stock_holding_vehicle_id_tenant_id", table_name="vehicle_stock_holding")
    op.drop_index("ix_vehicle_stock_holding_tenant_id", table_name="vehicle_stock_holding")
    op.drop_table("vehicle_stock_holding")
