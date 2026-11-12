"""vehicle_party.closed_for_party_id (KAN-644, KAN-583)

Anto, 2026-11-12: a holder recorded from a later date closes the current
holder at that start, not now (KAN-644 A); cancelling the planned holder
before it begins keeps it as «planned, never started» (KAN-583 1A) and the
current holder keeps the role. This column names, on the current holder's
row, the planned row whose start it was closed at — so a cancel gives the
role back to exactly that row, and a moved planned start moves its end, never
an end a user typed. A same-context GUID, no DB-level FK (PR-2, ADR-015),
NULL on every other row. No data step: no row was closed this way before.

The downgrade drops the column; rows closed at a planned start keep their
end and lose the link.

Revision ID: bd7cbe9376bc
Revises: 532af5864bb9
Create Date: 2026-11-12 07:37:13.440447

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "bd7cbe9376bc"
down_revision: Union[str, Sequence[str], None] = "532af5864bb9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMENT = (
    "Same context (customer): the planned vehicle_party row whose start this row was closed at "
    "(KAN-644); cleared when that handover is cancelled. KAN-583."
)


def upgrade() -> None:
    op.add_column(
        "vehicle_party",
        sa.Column("closed_for_party_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("vehicle_party", "closed_for_party_id")
