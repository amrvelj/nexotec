"""kan831 labour rate agreement set aside (KAN-831, ADR-232)

`labour_rate_agreement.set_aside_at`: a contact merge moves the duplicate's
agreements to the survivor; where the survivor already holds an agreed rate
(a live rate row) of a category in the Betrieb, the survivor's agreement survives (Anto, option
A, 2026-11-27) and every row of the duplicate for that category stays on the
duplicate's id, set aside.
Nullable, no backfill: no row was set aside before this revision.

Revision ID: b2899e03c44e
Revises: cfa3805abcd6
Create Date: 2026-11-26 10:51:31.295449

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.core.types


# revision identifiers, used by Alembic.
revision: str = 'b2899e03c44e'
down_revision: Union[str, Sequence[str], None] = 'cfa3805abcd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("labour_rate_agreement", sa.Column("set_aside_at", app.core.types.UTCDateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("labour_rate_agreement", "set_aside_at")
