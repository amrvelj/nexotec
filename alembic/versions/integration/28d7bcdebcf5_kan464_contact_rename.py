"""KAN-464 PR-3: the message delivery's contact columns and index

Customers became contacts (ADR-210; Anto, 2026-10-26: one register with roles, the full
rename). Renames only, in the catalogue: no row is rewritten, and every name is listed
explicitly because Postgres renames no index or constraint with its table or column.
Applied migrations keep the names of their day.

Revision ID: 28d7bcdebcf5
Revises: e8d68fa101f1
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "28d7bcdebcf5"
down_revision: str | Sequence[str] | None = "e8d68fa101f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table as it is named after the table renames, old column, new column)
_COLUMNS = (
    ("integration_message_delivery", "customer_id", "contact_id"),
    ("integration_message_delivery", "customer_label", "contact_label"),
    ("integration_message_delivery", "customer_label_refreshed_at", "contact_label_refreshed_at"),
)

# (old, new) — the plain and partial indexes
_INDEXES = (
    ("ix_integration_message_delivery_customer_id", "ix_integration_message_delivery_contact_id"),
)

# (table, column, comment after, comment before) — the owner comments that name the context
_COMMENTS = (
    (
        "integration_message_delivery",
        "contact_id",
        "Owned by the contact context (Contact.id). No DB-level FK.",
        "Owned by the customer context (Customer.id). No DB-level FK.",
    ),
)


def _comment(table: str, column: str, text: str | None) -> None:
    quoted = "NULL" if text is None else "'" + text.replace("'", "''") + "'"
    op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS {quoted}')


def upgrade() -> None:
    for table, old, new in _COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')
    for table, column, after, _before in _COMMENTS:
        _comment(table, column, after)


def downgrade() -> None:
    for table, column, _after, before in _COMMENTS:
        _comment(table, column, before)
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX "{new}" RENAME TO "{old}"')
    for table, old, new in _COLUMNS:
        op.alter_column(table, new, new_column_name=old)
