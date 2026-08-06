"""vehicle_custody_event composite index (vehicle_id, partner_id)

Adds an index to support the per-request "has this tenant ever touched
this vehicle" EXISTS check (Swiss addendum Round 3 visibility rule, R1
refinement — see services/vehicle.py::has_custody_event_for_tenant).
A new migration rather than amending the table's original migration
(1f209e4b5393) since that one is already merged to main.

Revision ID: ba2b87e95b1a
Revises: e5873926e0a9
Create Date: 2026-08-06 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ba2b87e95b1a'
down_revision: Union[str, Sequence[str], None] = 'e5873926e0a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_vehicle_custody_event_vehicle_id_partner_id",
        "vehicle_custody_event",
        ["vehicle_id", "partner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vehicle_custody_event_vehicle_id_partner_id", table_name="vehicle_custody_event")
