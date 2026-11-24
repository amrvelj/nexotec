"""KAN-464 PR-3: an owner comment that named a customer model

Customers became contacts (ADR-210; Anto, 2026-10-26: one register with roles, the full
rename). Renames only, in the catalogue: no row is rewritten, and every name is listed
explicitly because Postgres renames no index or constraint with its table or column.
Applied migrations keep the names of their day.

Revision ID: 792de2ffce6f
Revises: 5d0b8e3f71a2
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "792de2ffce6f"
down_revision: str | Sequence[str] | None = "5d0b8e3f71a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, comment after, comment before) — the owner comments that name the context
_COMMENTS = (
    (
        "vehicle_dealer_plate_assignment",
        "tenant_id",
        "Denormalized from DealerPlate.tenant_id at insert time — same reasoning as ContactPhone.group_id.",
        "Denormalized from DealerPlate.tenant_id at insert time — same reasoning as CustomerPhone.group_id.",
    ),
)


def _comment(table: str, column: str, text: str | None) -> None:
    quoted = "NULL" if text is None else "'" + text.replace("'", "''") + "'"
    op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS {quoted}')


def upgrade() -> None:
    for table, column, after, _before in _COMMENTS:
        _comment(table, column, after)


def downgrade() -> None:
    for table, column, _after, before in _COMMENTS:
        _comment(table, column, before)
