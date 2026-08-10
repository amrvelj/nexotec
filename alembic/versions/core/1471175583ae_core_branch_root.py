"""core branch root (PR-3, ADR-015)

Branches app.core's own migration chain forward from b36486886126 — the
frozen shared trunk (19 revisions, pre-split). Starts empty on purpose:
core's existing tables (audit_event, idempotency_record,
reconciliation_run, reconciliation_orphan) were all created before this
split and stay in the trunk. PR-4's transactional outbox is the first
thing that lands on this chain — one shared outbox table, not one per
context, matching the pattern audit/idempotency/reconciliation already
established: these are core-owned tables every context writes to, not a
seam violation, since app.core is the shared platform layer the
import-linter contract deliberately never forbade. Splitting it at
extraction time is close to free, since outbox rows are transient — the
new service gets an empty table and the old one drains.

Revision ID: 1471175583ae
Revises: b36486886126
Create Date: 2026-08-10 14:55:30.576629

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1471175583ae'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('core',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
