"""vehicle_mdm: a pre-1981 car's chassis-number record (KAN-535, step 1 of 2)

ADR-103 as amended (Anto, 2026-11-13, option A): a car first registered before
1981 that carries a chassis number instead of an ISO 3779 VIN (KAN-353) gets a
vehicle-mdm record when it is bought onto stock. Its record is that number
together with its make, compared tolerantly.

This step adds what a record says about itself:

- `identifier_kind` (`vin` | `chassis_number`, a StoredEnum with its
  `ck_vehicle_mdm_identifier_kind_enum`), NOT NULL, every existing row `vin`
  through the server default — a metadata-only change on Postgres 11+.
- `chassis_make_key`, the make's comparison key, null on a VIN record.
- `ck_vehicle_mdm_chassis_number_identity`: a VIN record has no make key; a
  chassis-number record has one and a first registration before 1981-01-01.
  Every existing row is a VIN record without a key, so the check holds.

Step 2 (`5d0b8e3f71a2`) replaces the single unique VIN constraint with one
unique index per kind, built CONCURRENTLY.

Revision ID: acf348f7a406
Revises: 778849af53d8
Create Date: 2026-11-14 00:41:23.642082

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "acf348f7a406"
down_revision: str | Sequence[str] | None = "778849af53d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vehicle_mdm"
# enum_check_name / enum_check_condition's exact wording (tests/test_migrated_enum_checks.py).
_KIND_CHECK = "ck_vehicle_mdm_identifier_kind_enum"
_KIND_CONDITION = "\"identifier_kind\" IN ('vin', 'chassis_number')"
# app/vehicle/models/vehicle_mdm.py::CHASSIS_NUMBER_IDENTITY_CONDITION, as a literal (ADR-133).
_IDENTITY_CHECK = "ck_vehicle_mdm_chassis_number_identity"
_IDENTITY_CONDITION = (
    "(identifier_kind = 'vin' AND chassis_make_key IS NULL) OR "
    "(identifier_kind = 'chassis_number' AND chassis_make_key IS NOT NULL "
    "AND first_registration_date IS NOT NULL AND first_registration_date < '1981-01-01')"
)


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "identifier_kind",
            sa.String(length=16),
            server_default="vin",
            nullable=False,
            comment="KAN-535: what `vin` holds — an ISO 3779 VIN, or a pre-1981 car's chassis number.",
        ),
    )
    op.create_check_constraint(_KIND_CHECK, _TABLE, _KIND_CONDITION)
    op.add_column(
        _TABLE,
        sa.Column(
            "chassis_make_key",
            sa.String(length=255),
            nullable=True,
            comment=(
                "KAN-535: a chassis-number record's make as typed on its configuration, compared tolerantly "
                "(case, spaces, hyphens and accents ignored); with `vin` its identity. Null on a VIN record."
            ),
        ),
    )
    op.create_check_constraint(_IDENTITY_CHECK, _TABLE, _IDENTITY_CONDITION)


def downgrade() -> None:
    # Step 2's downgrade has already refused a chassis-number record that
    # shares its number with another record; dropping the kind turns the
    # rest into plain records under the restored unique VIN constraint.
    op.drop_constraint(_IDENTITY_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "chassis_make_key")
    op.drop_constraint(_KIND_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "identifier_kind")
