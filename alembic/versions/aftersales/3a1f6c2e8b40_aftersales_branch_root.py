"""aftersales branch root (KAN-810, ADR-015)

Branches the aftersales context's own migration chain forward from
b36486886126 — the frozen shared trunk. No schema change; this revision
exists only to establish the branch point. The workshop order's tables
(KAN-810) descend from here, in alembic/versions/aftersales/, and never
touch another context's tables.

Revision ID: 3a1f6c2e8b40
Revises: b36486886126
Create Date: 2026-11-25 15:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '3a1f6c2e8b40'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('aftersales',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
