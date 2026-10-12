"""vehicle_party.merged_into_customer_id (KAN-177)

ADR-064: party history is never deleted. A customer merge re-points the
duplicate's vehicle_party rows at the survivor, but a row whose (vehicle,
role, effective_from) the survivor already holds cannot be re-pointed —
uq_vehicle_party_scope spans customer_id — and until KAN-177 the merge
hard-deleted it. Anto's ruling (2026-10-12, option A): keep the row on the
duplicate's customer_id, closed at its own start, and mark which survivor
it was merged into. This column is that mark: a same-context GUID, no
DB-level FK (PR-2, ADR-015), NULL on every row a merge did not keep this
way. No data step: no existing row was kept — the deleted ones are gone.

The downgrade drops the column; the kept rows stay, closed, and lose the
mark (the merge's audit event still lists their ids).

Revision ID: 532af5864bb9
Revises: 75bbdc34d9c8
Create Date: 2026-10-12 19:40:34.592675

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "532af5864bb9"
down_revision: Union[str, Sequence[str], None] = "75bbdc34d9c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMENT = (
    "Same context (customer): the survivor this row's customer was merged into, set only on a copy "
    "the merge kept closed because the survivor already held its (vehicle, role, effective_from). KAN-177."
)


def upgrade() -> None:
    op.add_column(
        "vehicle_party",
        sa.Column("merged_into_customer_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("vehicle_party", "merged_into_customer_id")
