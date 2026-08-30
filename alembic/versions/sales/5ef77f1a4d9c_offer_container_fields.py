"""sales_offer: vehicle_source + manual config + leasing inputs (WP-8 PR-2,
FR-S-08, S-D03)

Revision ID: 5ef77f1a4d9c
Revises: 233188a37b11
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5ef77f1a4d9c'
down_revision: Union[str, Sequence[str], None] = '233188a37b11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_offer", sa.Column("vehicle_source", sa.String(length=16), nullable=True))
    op.add_column("sales_offer", sa.Column("manual_vehicle_condition", sa.String(length=16), nullable=True))
    op.add_column("sales_offer", sa.Column("leasing_down_payment", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_offer", sa.Column("leasing_term_months", sa.Integer(), nullable=True))
    op.add_column("sales_offer", sa.Column("leasing_km_per_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_offer", "leasing_km_per_year")
    op.drop_column("sales_offer", "leasing_term_months")
    op.drop_column("sales_offer", "leasing_down_payment")
    op.drop_column("sales_offer", "manual_vehicle_condition")
    op.drop_column("sales_offer", "vehicle_source")
