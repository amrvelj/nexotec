"""dealership_membership.is_dealer_manager (KAN-98, D-A-01)

The manager flag is held per dealership (Dealer Administration PRD,
organisation model and D-A-01). A user's home dealership keeps its flag on
User.is_dealer_manager; every ADDITIONAL dealership a membership grants now
carries its own flag here, and switch-dealership mints the target's one.

Defaults false for every existing membership and is deliberately not
backfilled from User.is_dealer_manager: copying the home flag across is
exactly the defect this fixes (a manager of A was a manager in every sister
dealership they could switch to).

Revision ID: c4e8a1f7b3d6
Revises: d7b1f4e02a96
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f7b3d6'
down_revision: Union[str, Sequence[str], None] = 'd7b1f4e02a96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dealership_membership",
        sa.Column("is_dealer_manager", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("dealership_membership", "is_dealer_manager")
