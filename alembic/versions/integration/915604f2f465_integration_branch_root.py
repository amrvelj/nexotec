"""integration branch root (WP-6 PR-1, ADR-015)

Branches the new integration context's own migration chain forward from
b36486886126 — the frozen shared trunk every other context's branch also
forks from. No schema change; this revision exists only to establish the
branch point. New integration migrations descend from here, in
alembic/versions/integration/, never from anything else, and never touch
another context's tables. `integration` is the 12th bounded context (see
CLAUDE.md's own updated table) — the generic secrets/connections/
entitlements/call-log registry (Integrations & API Credentials v0.1's own
"one registry, many gateways" split) plus the auto-i-dat-specific gateway
logic, both landing here rather than inside app.vehicle.

Revision ID: 915604f2f465
Revises: b36486886126
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '915604f2f465'
down_revision: Union[str, Sequence[str], None] = 'b36486886126'
branch_labels: Union[str, Sequence[str], None] = ('integration',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
