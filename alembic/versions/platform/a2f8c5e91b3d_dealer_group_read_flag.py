"""dealer_group.group_read_enabled (WP-3 PR-4, ADR-030)

Defaults false for every existing group — platform_admin flips it on
per-group only once a legal_basis row exists (app.platform.services.
dealership.enable_group_read's own precondition).

Revision ID: a2f8c5e91b3d
Revises: e7c3a48f1d92
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2f8c5e91b3d'
down_revision: Union[str, Sequence[str], None] = 'e7c3a48f1d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dealer_group",
        sa.Column("group_read_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("dealer_group", "group_read_enabled")
