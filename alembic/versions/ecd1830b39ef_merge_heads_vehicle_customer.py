"""merge heads: vehicle + customer

Revision ID: ecd1830b39ef
Revises: 1f209e4b5393, bdfe43d537fd
Create Date: 2026-08-06 13:47:04.882666

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ecd1830b39ef'
down_revision: Union[str, Sequence[str], None] = ('1f209e4b5393', 'bdfe43d537fd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
