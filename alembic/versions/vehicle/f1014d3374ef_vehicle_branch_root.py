"""vehicle branch root (PR-3, ADR-015)

Branches the vehicle context's own migration chain forward from
b36486886126 — the frozen shared trunk (19 revisions, pre-split). No
schema change; this revision exists only to establish the branch point.
New vehicle migrations descend from here, in alembic/versions/vehicle/,
never from anything else, and never touch another context's tables.

Revision ID: f1014d3374ef
Revises: b36486886126
Create Date: 2026-08-10 14:55:30.044407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1014d3374ef'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('vehicle',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
