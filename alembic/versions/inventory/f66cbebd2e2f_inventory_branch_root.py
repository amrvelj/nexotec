"""inventory branch root (WP-7 PR-1, ADR-015)

Branches the inventory context's own migration chain forward from
b36486886126 — the frozen shared trunk. No schema change; this revision
exists only to establish the branch point. New inventory migrations
descend from here, in alembic/versions/inventory/, never from anything
else, and never touch another context's tables.

Revision ID: f66cbebd2e2f
Revises: b36486886126
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f66cbebd2e2f'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('inventory',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
