"""reference_value.label_en (WP-1, Gap Analysis G-18)

Adds label_en and backfills every existing row seeded by
c9654d846ac9_reference_list_and_reference_value_.py — that migration is the
only place reference_value rows have ever been seeded, so this is a
complete, closed backfill by (list_code, value_code), not a sample.

Revision ID: f2a6c1b7de3c
Revises: 75ee9234806b
Create Date: 2026-08-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a6c1b7de3c'
down_revision: Union[str, Sequence[str], None] = '75ee9234806b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# English labels for every (list_code, value_code) pair seeded by
# c9654d846ac9. Brand names in oem_affiliations are identical across every
# label, English included, same reasoning as the other three languages in
# that migration — manufacturer names aren't translated.
ENGLISH_LABELS: dict[str, dict[str, str]] = {
    "vehicle_type": {
        "passenger_car": "Passenger car",
        "light_commercial": "Light commercial vehicle",
        "truck": "Truck",
        "motorcycle": "Motorcycle",
    },
    "fuel_type": {
        "petrol": "Petrol",
        "diesel": "Diesel",
        "electric": "Electric",
        "hybrid": "Hybrid",
        "plugin_hybrid": "Plug-in hybrid",
        "hydrogen": "Hydrogen",
    },
    "body_style": {
        "sedan": "Sedan",
        "hatchback": "Hatchback",
        "suv": "SUV",
        "estate": "Estate",
        "convertible": "Convertible",
        "coupe": "Coupe",
        "van": "Van",
        "pickup": "Pickup",
    },
    "drivetrain": {
        "fwd": "Front-wheel drive",
        "rwd": "Rear-wheel drive",
        "awd": "All-wheel drive",
    },
    "transmission": {
        "manual": "Manual transmission",
        "automatic": "Automatic transmission",
        "semi_automatic": "Semi-automatic transmission",
        "cvt": "Continuously variable transmission (CVT)",
    },
    "exterior_color": {
        "black": "Black",
        "white": "White",
        "silver": "Silver",
        "grey": "Grey",
        "blue": "Blue",
        "red": "Red",
        "green": "Green",
        "brown": "Brown",
        "beige": "Beige",
        "yellow": "Yellow",
    },
    "interior_color": {
        "black": "Black",
        "grey": "Grey",
        "beige": "Beige",
        "brown": "Brown",
        "red": "Red",
        "white": "White",
    },
    "oem_affiliations": {
        "volkswagen": "Volkswagen",
        "bmw": "BMW",
        "mercedes_benz": "Mercedes-Benz",
        "audi": "Audi",
        "skoda": "Škoda",
        "renault": "Renault",
        "peugeot": "Peugeot",
        "fiat": "Fiat",
        "toyota": "Toyota",
        "ford": "Ford",
    },
}


def upgrade() -> None:
    op.add_column("reference_value", sa.Column("label_en", sa.String(length=200), nullable=True))

    reference_list = sa.table(
        "reference_list", sa.column("id", sa.String), sa.column("list_code", sa.String)
    )
    reference_value = sa.table(
        "reference_value",
        sa.column("list_id", sa.String),
        sa.column("value_code", sa.String),
        sa.column("label_en", sa.String),
    )

    # op.execute(), not connection.execute() — this is what renders as
    # literal-bind static SQL under `alembic upgrade --sql` (offline mode);
    # a raw connection.execute() has no bind to run against there.
    for list_code, values in ENGLISH_LABELS.items():
        for value_code, label_en in values.items():
            op.execute(
                reference_value.update()
                .where(
                    reference_value.c.list_id.in_(
                        sa.select(reference_list.c.id).where(reference_list.c.list_code == list_code)
                    ),
                    reference_value.c.value_code == value_code,
                )
                .values(label_en=label_en)
            )

    # Every row c9654d846ac9 ever seeded is covered by ENGLISH_LABELS above,
    # so nullable=False can be enforced immediately — nothing else has ever
    # written a reference_value row (see this file's own docstring).
    op.alter_column("reference_value", "label_en", nullable=False)


def downgrade() -> None:
    op.drop_column("reference_value", "label_en")
