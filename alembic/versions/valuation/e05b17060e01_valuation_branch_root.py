"""valuation branch root (WP-8 PR-5, ADR-015)

Branches the new valuation context's own migration chain forward from
b36486886126 — the frozen shared trunk. No schema change; this revision
exists only to establish the branch point. New valuation migrations
descend from here, in alembic/versions/valuation/, never from anything
else, and never touch another context's tables. `valuation` is the 11th
bounded context (see CLAUDE.md's own updated table) — the first new
top-level package to be added since the ten were first enumerated.

Revision ID: e05b17060e01
Revises: b36486886126
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'e05b17060e01'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('valuation',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
