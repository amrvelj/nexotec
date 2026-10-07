"""KAN-84 — vehicle_party carries the vehicle's label (CLAUDE.md rule 2)

VehicleParty.vehicle was a viewonly relationship() joining customer's
vehicle_party to vehicle's vehicle_mdm at read time — the cross-context JOIN
rule 2 forbids. It is replaced by the three-column pattern: the vehicle's
display fields denormalised onto the party row plus a refreshed-at stamp.

All columns are nullable and are NOT backfilled here: the values live in
another context's tables, and a migration on customer's chain does not read
them. app.customer.services.customer.refresh_vehicle_party_labels (the
nightly `customer.vehicle_party_labels` job) is the backfill; until it has
run, the read path fills an unlabelled row in memory from
app.vehicle.public.

Revision ID: c84a1e5d7b20
Revises: a3f8d2c91b64
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c84a1e5d7b20"
down_revision: Union[str, Sequence[str], None] = "a3f8d2c91b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("vehicle_vin", sa.String(length=17), "Label from the vehicle context (vehicle_mdm.vin). KAN-84."),
    ("vehicle_number", sa.String(length=16), "Label from the vehicle context (vehicle_mdm.vehicle_number). KAN-84."),
    ("vehicle_make", sa.String(length=120), "Label from the vehicle context (catalogue brand). KAN-84."),
    ("vehicle_model", sa.String(length=120), "Label from the vehicle context (catalogue model group). KAN-84."),
    ("vehicle_model_year", sa.Integer(), "Label from the vehicle context (first registration year). KAN-84."),
    ("vehicle_trim", sa.String(length=160), "Label from the vehicle context (catalogue variant name). KAN-84."),
    (
        "vehicle_label_refreshed_at",
        sa.DateTime(timezone=True),
        "When the vehicle_* labels were last read from the vehicle context.",
    ),
]


def upgrade() -> None:
    for name, type_, comment in _COLUMNS:
        op.add_column("vehicle_party", sa.Column(name, type_, nullable=True, comment=comment))


def downgrade() -> None:
    for name, _, _ in reversed(_COLUMNS):
        op.drop_column("vehicle_party", name)
