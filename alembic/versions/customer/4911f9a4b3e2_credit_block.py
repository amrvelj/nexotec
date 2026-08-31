"""customer.credit_block (WP-8 PR-6, ADR-065/S-D19)

Revision ID: 4911f9a4b3e2
Revises: c7e2a91f4b06
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4911f9a4b3e2'
down_revision: Union[str, Sequence[str], None] = 'c7e2a91f4b06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customer", sa.Column("credit_block", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("customer", sa.Column("credit_block_reason", sa.String(length=500), nullable=True))
    op.add_column("customer", sa.Column("credit_blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("customer", "credit_block", server_default=None)


def downgrade() -> None:
    op.drop_column("customer", "credit_blocked_at")
    op.drop_column("customer", "credit_block_reason")
    op.drop_column("customer", "credit_block")
