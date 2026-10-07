"""KAN-161 — indexes for the vehicle list's two status sort keys.

The vehicle list (`GET /v1/vehicle-mdm/search`) makes every grid column
sortable, and U-03 is explicit: "a column with no index is not shipped as
sortable." `vehicle_number`, `vin` and `stammnummer` were indexed when the
table was created (rev 5c7e33d9cc78); this adds the btree indexes behind
`vehicleStatus` and `catalogueMatchStatus`, the allow-list in
`app/vehicle/api/vehicle_mdm.py::VEHICLE_MDM_SORT_FIELDS`.

Revision ID: a9d4c2e7f1b3
Revises: 7c4e9a2b6d13
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d4c2e7f1b3"
down_revision: Union[str, Sequence[str], None] = "7c4e9a2b6d13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES: list[tuple[str, str]] = [
    ("ix_vehicle_mdm_vehicle_status", "vehicle_status"),
    ("ix_vehicle_mdm_catalogue_match_status", "catalogue_match_status"),
]


def upgrade() -> None:
    for name, column in _INDEXES:
        op.create_index(name, "vehicle_mdm", [column])


def downgrade() -> None:
    for name, _column in reversed(_INDEXES):
        op.drop_index(name, table_name="vehicle_mdm")
