"""vehicle_mdm marketplace equipment fields (WP-7 PR-8, ADR-062)

Added by inventory (WP-7), the first and only consumer — equipment is a
fact about the car, so it belongs here, not on the publishing tables.
Three genuinely separate concepts, never merged: ausstattung_codes
(searchable codes), extras (boolean-flag features), eigenschaften
(condition/status flags like Unfallwagen) — plus a free-text, tri-lingual
provider_ausstattung. "AutoScout24 kennt drei getrennte Begriffe... weil
ein Unfall keine Ausstattung ist," confirmed verbatim in the live
reference prototype's own publishing tab.

Revision ID: 1428f5f37b66
Revises: 0d1d8416b8b7
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1428f5f37b66'
down_revision: Union[str, Sequence[str], None] = '0d1d8416b8b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicle_mdm", sa.Column("ausstattung_codes", sa.JSON(), nullable=True))
    op.add_column("vehicle_mdm", sa.Column("extras", sa.JSON(), nullable=True))
    op.add_column("vehicle_mdm", sa.Column("eigenschaften", sa.JSON(), nullable=True))
    op.add_column("vehicle_mdm", sa.Column("provider_ausstattung", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_mdm", "provider_ausstattung")
    op.drop_column("vehicle_mdm", "eigenschaften")
    op.drop_column("vehicle_mdm", "extras")
    op.drop_column("vehicle_mdm", "ausstattung_codes")
