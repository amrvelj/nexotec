"""dealership.ageing_alert_thresholds (WP-7 PR-7, FR-I-14)

Genuinely dealer-configurable, unlike the fixed ageingBucket grid colour
cue (0-60/61-120/121+) — same underlying "days in stock" number, a
completely separate consumer (notifications, not the grid).

Revision ID: 46c6382bdbf5
Revises: 51cf71d1e96c
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '46c6382bdbf5'
down_revision: Union[str, Sequence[str], None] = '51cf71d1e96c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dealership", sa.Column("ageing_alert_thresholds", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("dealership", "ageing_alert_thresholds")
