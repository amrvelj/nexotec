"""KAN-464 PR-3: Stock's trade-in contact column

Customers became contacts (ADR-210; Anto, 2026-10-26: one register with roles, the full
rename). Renames only, in the catalogue: no row is rewritten, and every name is listed
explicitly because Postgres renames no index or constraint with its table or column.
Applied migrations keep the names of their day.

Revision ID: f3dee38222c5
Revises: 2b287ef9c1e7
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3dee38222c5"
down_revision: str | Sequence[str] | None = "2b287ef9c1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table as it is named after the table renames, old column, new column)
_COLUMNS = (
    ("stock_item", "trade_in_customer_id", "trade_in_contact_id"),
)

# (table, column, comment after, comment before) — the owner comments that name the context
_COMMENTS = (
    (
        "stock_item",
        "trade_in_contact_id",
        "Owned by the contact context (Contact). No DB-level FK.",
        "Owned by the customer context (Customer). No DB-level FK.",
    ),
    (
        "stock_item",
        "supplier_contact_id",
        "Owned by the contact context (Contact.id, a contact with the supplier role). No DB-level FK.",
        "Owned by the customer context (Customer.id, a contact with the supplier role). No DB-level FK.",
    ),
)


def _comment(table: str, column: str, text: str | None) -> None:
    quoted = "NULL" if text is None else "'" + text.replace("'", "''") + "'"
    op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS {quoted}')


def upgrade() -> None:
    for table, old, new in _COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for table, column, after, _before in _COMMENTS:
        _comment(table, column, after)


def downgrade() -> None:
    for table, column, _after, before in _COMMENTS:
        _comment(table, column, before)
    for table, old, new in _COLUMNS:
        op.alter_column(table, new, new_column_name=old)
