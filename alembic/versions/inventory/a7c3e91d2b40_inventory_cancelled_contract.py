"""inventory_cancelled_contract — contracts Stock knows to be cancelled (KAN-158)

Written by the `sales.contract.cancelled` consumer, read by the
`sales.contract.confirmed` consumer, so a confirmation delivered after its
contract's cancellation never creates the ordered car reserved. Schema
only: an empty table, no data moved.

Revision ID: a7c3e91d2b40
Revises: 6f1a3c8b9d2e
Create Date: 2026-10-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c3e91d2b40'
down_revision: Union[str, Sequence[str], None] = '6f1a3c8b9d2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_cancelled_contract",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "contract_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the sales context (SalesContract.id). No DB-level FK.",
        ),
        sa.Column("contract_label", sa.String(length=16), nullable=False),
        sa.Column("contract_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_cancelled_contract_tenant_id", "inventory_cancelled_contract", ["tenant_id"])
    op.create_unique_constraint(
        "uq_inventory_cancelled_contract_tenant_id_contract_id",
        "inventory_cancelled_contract",
        ["tenant_id", "contract_id"],
    )


def downgrade() -> None:
    op.drop_table("inventory_cancelled_contract")
