"""merge heads: transaction/vehicle fixes + credential

Revision ID: 45e33bf8cdbf
Revises: 5b73bc64c450, f566acd37d91
Create Date: 2026-08-06 14:48:54.250293

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45e33bf8cdbf'
down_revision: Union[str, Sequence[str], None] = ('5b73bc64c450', 'f566acd37d91')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
