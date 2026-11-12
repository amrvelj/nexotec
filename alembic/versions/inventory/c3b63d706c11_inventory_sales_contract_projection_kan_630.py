"""inventory_sales_contract — Stock's projection of Sales' contracts (KAN-630)

ADR-131 puts Stock below Sales, so Stock's orphan sweep and «Einkauf
stornieren» stop asking Sales; they read this projection instead, fed by
`sales.contract.created` / `.confirmed` / `.invoice_requested` /
`.cancelled` through the outbox. `inventory_sales_contract_backfill`
records each committed run of
`scripts/backfill_inventory_sales_contract_projection.py`, which fills the
projection for contracts that existed before. Schema only: two empty
tables, no data moved and no other context's table read (the backfill is
the script's job), so it renders under `--sql` too.

Revision ID: c3b63d706c11
Revises: 80667c6fddf8
Create Date: 2026-11-12 17:05:52.902144

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3b63d706c11"
down_revision: Union[str, Sequence[str], None] = "80667c6fddf8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_sales_contract",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "contract_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the sales context (SalesContract.id). No DB-level FK.",
        ),
        sa.Column("contract_label", sa.String(length=16), nullable=True),
        sa.Column("contract_label_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stock_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "\"status\" IN ('pending', 'confirmed', 'invoiced', 'cancelled')",
            name="ck_inventory_sales_contract_status_enum",
        ),
        sa.UniqueConstraint("tenant_id", "contract_id", name="uq_inventory_sales_contract_tenant_id_contract_id"),
    )
    op.create_index("ix_inventory_sales_contract_tenant_id", "inventory_sales_contract", ["tenant_id"])
    op.create_index(
        "ix_inventory_sales_contract_tenant_id_stock_item_id",
        "inventory_sales_contract",
        ["tenant_id", "stock_item_id"],
    )
    op.create_table(
        "inventory_sales_contract_backfill",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contracts_read", sa.Integer(), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("inventory_sales_contract_backfill")
    op.drop_table("inventory_sales_contract")
