"""merge heads: vehicle + credential

Revision ID: 5b73bc64c450
Revises: 1f209e4b5393, e51ba97525e0
Create Date: 2026-08-06 13:48:46.172120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b73bc64c450'
down_revision: Union[str, Sequence[str], None] = ('1f209e4b5393', 'e51ba97525e0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
