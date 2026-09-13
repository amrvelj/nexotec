"""VehicleConfiguration.wheels / wheels_surcharge (KAN-43, Configurator C-E, FR-C-08)

Mirrors the exterior/interior colour pair C-C already shipped —
`app/vehicle/models/configuration.py::VehicleConfiguration`.

Revision ID: 9d15e6a4c2f0
Revises: 6f3e29a7c8b1
Create Date: 2026-09-13 00:00:00.000002

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d15e6a4c2f0"
down_revision: Union[str, Sequence[str], None] = "6f3e29a7c8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicle_configuration", sa.Column("wheels", sa.String(length=120), nullable=True))
    op.add_column("vehicle_configuration", sa.Column("wheels_surcharge", sa.DECIMAL(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_configuration", "wheels_surcharge")
    op.drop_column("vehicle_configuration", "wheels")
