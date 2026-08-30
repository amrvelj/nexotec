"""stock_item_ledger — the Wagenbuch (WP-7 PR-6, ADR-029)

Append-only, entity-private (never group-readable). Unique
(tenant_id, source_ref) is recordCost's own idempotency key.

Revision ID: ce4b8c411730
Revises: 8b267a737fc8
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ce4b8c411730'
down_revision: Union[str, Sequence[str], None] = '8b267a737fc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_item_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column("stock_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_item.id"), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("stock_item_ledger", "is_auto", server_default=None)
    op.create_index("ix_stock_item_ledger_tenant_id", "stock_item_ledger", ["tenant_id"])
    op.create_index("ix_stock_item_ledger_stock_item_id", "stock_item_ledger", ["stock_item_id"])
    op.create_unique_constraint(
        "uq_stock_item_ledger_tenant_id_source_ref", "stock_item_ledger", ["tenant_id", "source_ref"]
    )


def downgrade() -> None:
    op.drop_table("stock_item_ledger")
