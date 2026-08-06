"""merge heads: vehicle + customer

Revision ID: 44a979f9f37b
Revises: 1f209e4b5393, bdfe43d537fd
Create Date: 2026-08-06 13:19:24.561074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44a979f9f37b'
down_revision: Union[str, Sequence[str], None] = ('1f209e4b5393', 'bdfe43d537fd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
