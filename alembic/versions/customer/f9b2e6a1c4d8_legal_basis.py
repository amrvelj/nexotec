"""legal_basis (WP-3 PR-4, ADR-030)

revDSG joint-controllership evidence per customer per group. Append-only —
see app.customer.models.legal_basis's own docstring for why there's no
UPDATE path, only INSERT.

Revision ID: f9b2e6a1c4d8
Revises: d4a1f6c8e3b7
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f9b2e6a1c4d8'
down_revision: Union[str, Sequence[str], None] = 'd4a1f6c8e3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legal_basis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (DealerGroup). No DB-level FK.",
        ),
        sa.Column("basis", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_legal_basis_customer_id", "legal_basis", ["customer_id"])
    op.create_index("ix_legal_basis_group_id", "legal_basis", ["group_id"])


def downgrade() -> None:
    op.drop_table("legal_basis")
