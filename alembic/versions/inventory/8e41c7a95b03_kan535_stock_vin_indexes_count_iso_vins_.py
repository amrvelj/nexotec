"""stock_item: the VIN index counts ISO VINs, a chassis-number car its record (KAN-535)

KAN-111 keeps a VIN in a dealership's stock at most once
(`uq_stock_item_tenant_id_vin_in_stock`). Since KAN-535 (ADR-103 as amended;
Anto, 2026-11-13, option A) a pre-1981 car bought onto stock may carry a
chassis number, unique only together with its make: two cars of two makes may
share one. So:

- `uq_stock_item_tenant_id_iso_vin_in_stock`: the same rule, for an ISO 3779
  VIN only (the pattern of `app.core.validators._VIN_RE`).
- `uq_stock_item_tenant_id_chassis_vehicle_in_stock`: a car whose `vin` is no
  ISO VIN is in the dealership's stock at most once by its vehicle-mdm record.
  No such item is in stock before this revision (a chassis number could not be
  booked), so the build cannot fail on existing rows.

stock_item is hot: both are built CONCURRENTLY in an autocommit block, each
after a DROP INDEX CONCURRENTLY IF EXISTS so a rerun repairs an INVALID one,
and the old index is dropped only after both hold (ADR-134; migrations.md).
The downgrade rebuilds the old index first, the same way; it fails while two
chassis-number cars of two makes share a number in one dealership's stock.

Revision ID: 8e41c7a95b03
Revises: 04ab4cc24646
Create Date: 2026-11-14 00:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e41c7a95b03"
down_revision: str | Sequence[str] | None = "04ab4cc24646"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "stock_item"
_OLD = "uq_stock_item_tenant_id_vin_in_stock"
_ISO_VIN = "uq_stock_item_tenant_id_iso_vin_in_stock"
_CHASSIS = "uq_stock_item_tenant_id_chassis_vehicle_in_stock"
# app/inventory/models/stock_item.py::ISO_VIN_PATTERN, as a literal (ADR-133).
_PATTERN = "^[A-HJ-NPR-Z0-9]{17}$"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_ISO_VIN}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_ISO_VIN} ON {_TABLE} (tenant_id, vin) "
            f"WHERE vin ~ '{_PATTERN}' AND left_stock_at IS NULL"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_CHASSIS}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_CHASSIS} ON {_TABLE} (tenant_id, vehicle_id) "
            f"WHERE vehicle_id IS NOT NULL AND vin !~ '{_PATTERN}' AND left_stock_at IS NULL"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_OLD} ON {_TABLE} (tenant_id, vin) "
            "WHERE vin IS NOT NULL AND left_stock_at IS NULL"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_CHASSIS}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_ISO_VIN}")
