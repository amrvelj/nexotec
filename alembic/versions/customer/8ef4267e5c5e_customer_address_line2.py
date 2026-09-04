"""customer.customer_address.address_line2 (FR-17)

Revision ID: 8ef4267e5c5e
Revises: 4911f9a4b3e2
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8ef4267e5c5e'
down_revision: Union[str, Sequence[str], None] = '4911f9a4b3e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customer_address", sa.Column("address_line2", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("customer_address", "address_line2")
