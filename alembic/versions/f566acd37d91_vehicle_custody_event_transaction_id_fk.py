"""vehicle_custody_event.transaction_id FK to transaction.id

Was a bare UUID forward reference on the original Vehicle migration
(1f209e4b5393, already merged to main), added before Transaction (issue #6)
existed. Now that it does, tighten the constraint via a new migration
rather than editing the merged one (CTO review, 2026-08-06).

Revision ID: f566acd37d91
Revises: ba2b87e95b1a
Create Date: 2026-08-06 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f566acd37d91'
down_revision: Union[str, Sequence[str], None] = 'ba2b87e95b1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_vehicle_custody_event_transaction_id_transaction",
        "vehicle_custody_event",
        "transaction",
        ["transaction_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_vehicle_custody_event_transaction_id_transaction", "vehicle_custody_event", type_="foreignkey"
    )
