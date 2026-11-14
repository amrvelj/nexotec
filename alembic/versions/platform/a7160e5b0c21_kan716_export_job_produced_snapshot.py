"""KAN-716: export_job.produced_xid_floor and produced_lookback_from — what the export's snapshot may miss

Revision ID: a7160e5b0c21
Revises: f348a218c4a4
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7160e5b0c21"
down_revision: str | Sequence[str] | None = "f348a218c4a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("export_job", sa.Column("produced_xid_floor", sa.BigInteger(), nullable=True))
    op.add_column("export_job", sa.Column("produced_lookback_from", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("export_job", "produced_lookback_from")
    op.drop_column("export_job", "produced_xid_floor")
