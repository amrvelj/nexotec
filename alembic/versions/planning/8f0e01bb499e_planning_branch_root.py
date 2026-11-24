"""planning branch root (KAN-804, ADR-015)

Branches the planning context's own migration chain forward from
b36486886126 — the frozen shared trunk. No schema change; this revision
exists only to establish the branch point. planning is the thirteenth
bounded context (Decision A of Aftersales slice 1, amending ADR-131); its
tables come with KAN-808, descend from here, in alembic/versions/planning/,
and never touch another context's tables.

Revision ID: 8f0e01bb499e
Revises: b36486886126
Create Date: 2026-11-24 12:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '8f0e01bb499e'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('planning',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
