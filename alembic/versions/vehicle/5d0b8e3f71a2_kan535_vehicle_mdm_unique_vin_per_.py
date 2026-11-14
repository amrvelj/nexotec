"""vehicle_mdm: the VIN unique per identifier kind (KAN-535, step 2 of 2)

A VIN stays unique among VIN records (`uq_vehicle_mdm_vin`); a pre-1981 car's
chassis number is unique together with its make key among chassis-number
records (`uq_vehicle_mdm_chassis_number_make`) — short chassis numbers repeat
across makes (Anto, 2026-11-13, option A). The two partial unique indexes
replace the single constraint `vehicle_mdm_vin_key`.

vehicle_mdm holds every car, so the indexes are built CONCURRENTLY in an
autocommit block of their own (ADR-134; migrations.md): each starts with
DROP INDEX CONCURRENTLY IF EXISTS, so a rerun after a failure leaving an
INVALID index repairs it. The old constraint is dropped only once both new
indexes hold, so the VIN is never unguarded.

The downgrade rebuilds `vehicle_mdm_vin_key` the same way (a unique index
built CONCURRENTLY, then attached as the constraint) and fails — leaving the
new indexes in place — while two records share a number.

Revision ID: 5d0b8e3f71a2
Revises: acf348f7a406
Create Date: 2026-11-14 00:43:10.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d0b8e3f71a2"
down_revision: str | Sequence[str] | None = "acf348f7a406"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vehicle_mdm"
_OLD_CONSTRAINT = "vehicle_mdm_vin_key"
_VIN_INDEX = "uq_vehicle_mdm_vin"
_CHASSIS_INDEX = "uq_vehicle_mdm_chassis_number_make"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_VIN_INDEX}")
        op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY {_VIN_INDEX} ON {_TABLE} (vin) WHERE identifier_kind = 'vin'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_CHASSIS_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_CHASSIS_INDEX} ON {_TABLE} (vin, chassis_make_key) "
            "WHERE identifier_kind = 'chassis_number'"
        )
    op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_OLD_CONSTRAINT}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD_CONSTRAINT}")
        op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY {_OLD_CONSTRAINT} ON {_TABLE} (vin)")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_OLD_CONSTRAINT} UNIQUE USING INDEX {_OLD_CONSTRAINT}")
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_CHASSIS_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_VIN_INDEX}")
