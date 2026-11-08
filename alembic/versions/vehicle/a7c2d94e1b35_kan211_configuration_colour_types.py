"""KAN-211: the colour types an advisor names by hand on a configuration

The identAPI delivers a colour's name as the manufacturer wrote it and no
finish — there is no Métalisé flag on any of its endpoints — so the advisor
names the type (Anto, 2026-11-08: «show the colour name as delivered; we ask
the user to select manually the colour type, as well as for interior»).

- `vehicle_configuration.exterior_colour_type`: a value of the platform's
  `colour_type` list (solid / metallic / pearl / matte, 6ba0a99ed5c4).
- `vehicle_configuration.interior_colour_type`: a value of the new
  `interior_colour_type` list (platform 4c8e1f2a9d57 seeds it).

Both nullable: a configuration stored before this release has none, and a
free-text colour needs none. No data step.

Revision ID: a7c2d94e1b35
Revises: e6f4cba3ab86
Create Date: 2026-11-08 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2d94e1b35"
down_revision: str | Sequence[str] | None = "e6f4cba3ab86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicle_configuration", sa.Column("exterior_colour_type", sa.String(length=64), nullable=True))
    op.add_column("vehicle_configuration", sa.Column("interior_colour_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_configuration", "interior_colour_type")
    op.drop_column("vehicle_configuration", "exterior_colour_type")
