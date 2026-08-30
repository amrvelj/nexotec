"""dealership.vat_rate (WP-7 PR-3, ADR-057)

CLAUDE.md names this field path directly: "VAT is one line on the printed
document only, computed at the dealer-configurable dealer_settings.
vat_rate." No dealer_settings table exists (confirmed — no settings table
anywhere in app.platform), so this lands as a single nullable column
directly on Dealership, which already IS the tenant. A future accumulation
of more per-tenant settings is the trigger to split this into its own
table, not a reason to invent one for a single field today.

Revision ID: 51cf71d1e96c
Revises: eb38e2956aee
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '51cf71d1e96c'
down_revision: Union[str, Sequence[str], None] = 'eb38e2956aee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dealership", sa.Column("vat_rate", sa.DECIMAL(precision=5, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column("dealership", "vat_rate")
