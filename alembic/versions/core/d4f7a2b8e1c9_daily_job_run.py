"""daily_job_run (WP-6 PR-4) — the cross-cutting daily-job scheduler's own
bookkeeping table. See app/core/daily_scheduler.py's own module docstring
for why this piggybacks on the existing outbox-worker process rather than
a dedicated cron service.

Revision ID: d4f7a2b8e1c9
Revises: c1a5e8f2b4d6
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4f7a2b8e1c9'
down_revision: Union[str, Sequence[str], None] = 'c1a5e8f2b4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_job_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_daily_job_run_job_name", "daily_job_run", ["job_name"])
    op.create_unique_constraint("uq_daily_job_run_job_name_run_date", "daily_job_run", ["job_name", "run_date"])


def downgrade() -> None:
    op.drop_table("daily_job_run")
