"""reporting branch root (KAN-225, ADR-015)

Branches the reporting context's own migration chain forward from
b36486886126 — the frozen shared trunk. No schema change; this revision
exists only to establish the branch point. reporting is the first stub
context to ship tables (the public stock projection); its migrations descend
from here, in alembic/versions/reporting/, and never touch another
context's tables.

Revision ID: a4c2e91d7b30
Revises: b36486886126
Create Date: 2026-11-14 15:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'a4c2e91d7b30'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('reporting',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
