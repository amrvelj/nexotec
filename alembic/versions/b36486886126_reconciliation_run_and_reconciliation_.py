"""reconciliation_run and reconciliation_orphan tables

The compensating control (P-10) for the previous revision dropping the
nine cross-context foreign keys. Read-only from the application's side —
nothing but the reconciliation job itself ever writes to these tables.

Revision ID: b36486886126
Revises: 22708a77f565
Create Date: 2026-08-10 14:03:54.965669

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b36486886126'
down_revision: Union[str, Sequence[str], None] = '22708a77f565'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checks_run", sa.Integer(), nullable=False),
        sa.Column("orphans_found", sa.Integer(), nullable=False),
    )
    op.create_index(op.f("ix_reconciliation_run_context"), "reconciliation_run", ["context"], unique=False)

    op.create_table(
        "reconciliation_orphan",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("check_label", sa.String(length=128), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_table", sa.String(length=64), nullable=False),
        sa.Column("dangling_value", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["reconciliation_run.id"], name="fk_reconciliation_orphan_run_id_reconciliation_run"
        ),
    )
    op.create_index(op.f("ix_reconciliation_orphan_run_id"), "reconciliation_orphan", ["run_id"], unique=False)
    op.create_index(op.f("ix_reconciliation_orphan_context"), "reconciliation_orphan", ["context"], unique=False)


def downgrade() -> None:
    op.drop_table("reconciliation_orphan")
    op.drop_table("reconciliation_run")
