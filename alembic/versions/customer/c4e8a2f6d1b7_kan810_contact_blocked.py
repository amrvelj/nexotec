"""KAN-810: «Gesperrt» on the contact — a block of its own, beside the credit block

Anto, 2026-11-26 (KAN-810 Q1, option 1): a «Gesperrt» contact is refused as a
workshop order's customer or invoice recipient; the credit block only changes
how such an order is paid. Three columns, shaped like the credit block's. A
boolean added with a constant default is a catalogue-only change in Postgres,
so `contact` is not rewritten.

Revision ID: c4e8a2f6d1b7
Revises: b1d5f8a3c6e9
Create Date: 2026-11-26 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a2f6d1b7"
down_revision: str | Sequence[str] | None = "b1d5f8a3c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contact", sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("contact", sa.Column("blocked_reason", sa.String(length=500), nullable=True))
    op.add_column("contact", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("contact", "blocked", server_default=None)


def downgrade() -> None:
    op.drop_column("contact", "blocked_at")
    op.drop_column("contact", "blocked_reason")
    op.drop_column("contact", "blocked")
