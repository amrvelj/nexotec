"""platform branch root (PR-3, ADR-015)

Branches the platform context's own migration chain forward from
b36486886126 — the frozen shared trunk (19 revisions, pre-split). No
schema change; this revision exists only to establish the branch point.
New platform migrations descend from here, in alembic/versions/platform/,
never from anything else, and never touch another context's tables.

Revision ID: 75ee9234806b
Revises: b36486886126
Create Date: 2026-08-10 14:54:57.605889

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '75ee9234806b'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('platform',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
