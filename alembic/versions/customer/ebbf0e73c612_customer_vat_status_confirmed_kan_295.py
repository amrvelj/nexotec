"""customer vat_status_confirmed (KAN-295)

`customer.vat_registered` is a NOT NULL boolean defaulting to false, so
"recorded as not VAT-registered" cannot be told apart from "never
recorded". `vat_status_confirmed` is the checkbox someone ticks once they
have checked it (Anto, 2026-10-11). The legacy transaction import holds a
trade-in whose customer is not VAT-registered until it is ticked, because
the fiktiver Vorsteuerabzug it books is a tax figure.

Every existing row starts unconfirmed (false) — nobody has checked any of
them yet. The downgrade drops the column; whatever was ticked is lost.

Revision ID: ebbf0e73c612
Revises: 4372f773da31
Create Date: 2026-10-11 15:21:00.819340

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ebbf0e73c612"
down_revision: Union[str, Sequence[str], None] = "4372f773da31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer",
        sa.Column("vat_status_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("customer", "vat_status_confirmed")
