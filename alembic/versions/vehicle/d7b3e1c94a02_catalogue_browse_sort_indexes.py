"""Configurator C-B (KAN-40) — indexes for the catalogue-browse sort keys.

FR-C-01's results grid sorts on the standard contract, and U-03 is
explicit: "a column with no index is not shipped as sortable." The browse
sort allow-list (`app/vehicle/api/catalogue.py::CATALOGUE_VARIANT_SORT_FIELDS`)
offers `variantName`, `ps`, `kw`, `displacementCcm`, `basePrice`,
`modelYearFrom`, `updatedAt` — this migration adds the btree indexes that
back them. `model_group_id` (the drill-down scope) is already indexed
(rev 74b6c2794698).

Cheap — every column is on `vehicle_model_variant`, whose spec block landed
empty in C-A (KAN-39); no environment has catalogue-scale data yet.

Revision ID: d7b3e1c94a02
Revises: c1f7a2e9b3d4
Create Date: 2026-09-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7b3e1c94a02"
down_revision: Union[str, Sequence[str], None] = "c1f7a2e9b3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES: list[tuple[str, str]] = [
    ("ix_vehicle_model_variant_name", "name"),
    ("ix_vehicle_model_variant_ps", "ps"),
    ("ix_vehicle_model_variant_kw", "kw"),
    ("ix_vehicle_model_variant_displacement_ccm", "displacement_ccm"),
    ("ix_vehicle_model_variant_base_price", "base_price"),
    ("ix_vehicle_model_variant_model_year_from", "model_year_from"),
    ("ix_vehicle_model_variant_updated_at", "updated_at"),
]


def upgrade() -> None:
    for name, column in _INDEXES:
        op.create_index(name, "vehicle_model_variant", [column])


def downgrade() -> None:
    for name, _column in reversed(_INDEXES):
        op.drop_index(name, table_name="vehicle_model_variant")
