"""vehicle_party.merged_into_vehicle_id (KAN-672)

Anto, 2026-11-12 (option C): after a vehicle merge the holder of a role with
the later start stays current and the others are closed at its start. A
party row of the duplicate vehicle whose (customer, role, effective_from) the
survivor already carries cannot move — uq_vehicle_party_scope spans
vehicle_id — so, like the copy a customer merge keeps (KAN-177), it stays on
the duplicate's vehicle_id, closed at its own start, and this column names
the vehicle its car was merged into. The vehicle context owns the id: a plain
GUID, no DB-level FK (rule 2), NULL on every other row. No data step: no
merge has kept a row this way before (the move collided and dead-lettered).

The downgrade drops the column; a kept copy keeps its end and loses the mark.

Revision ID: e7a3c95b1d62
Revises: c4e8a1f7d932
Create Date: 2026-11-12 21:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7a3c95b1d62"
down_revision: Union[str, Sequence[str], None] = "c4e8a1f7d932"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMENT = (
    "Owned by the vehicle context (vehicle_mdm.id, no DB-level FK): the vehicle this row's vehicle was "
    "merged into, set only on a copy the vehicle merge kept closed because the survivor already carried "
    "its (customer, role, effective_from). KAN-672."
)


def upgrade() -> None:
    op.add_column(
        "vehicle_party",
        sa.Column("merged_into_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("vehicle_party", "merged_into_vehicle_id")
