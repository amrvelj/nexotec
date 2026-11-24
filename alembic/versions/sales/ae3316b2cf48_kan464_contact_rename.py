"""KAN-464 PR-3: Sales' contact columns (three-column pattern) and their indexes

Customers became contacts (ADR-209; Anto, 2026-10-26: one register with roles, the full
rename). Renames only, in the catalogue: no row is rewritten, and every name is listed
explicitly because Postgres renames no index or constraint with its table or column. The
applied history keeps its names, the chain its label `customer`.

Revision ID: ae3316b2cf48
Revises: f42313d790a2
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ae3316b2cf48"
down_revision: str | Sequence[str] | None = "f42313d790a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table as it is named after the table renames, old column, new column)
_COLUMNS = (
    ("sales_contract", "customer_denorm_refreshed_at", "contact_denorm_refreshed_at"),
    ("sales_contract", "customer_id", "contact_id"),
    ("sales_contract", "customer_label", "contact_label"),
    ("sales_contract", "customer_language", "contact_language"),
    ("sales_contract", "customer_locality", "contact_locality"),
    ("sales_deal", "customer_denorm_refreshed_at", "contact_denorm_refreshed_at"),
    ("sales_deal", "customer_id", "contact_id"),
    ("sales_deal", "customer_label", "contact_label"),
    ("sales_deal", "customer_locality", "contact_locality"),
    ("sales_offer", "customer_denorm_refreshed_at", "contact_denorm_refreshed_at"),
    ("sales_offer", "customer_id", "contact_id"),
    ("sales_offer", "customer_label", "contact_label"),
    ("sales_offer", "customer_language", "contact_language"),
    ("sales_offer", "customer_locality", "contact_locality"),
)

# (old, new) — the plain and partial indexes
_INDEXES = (
    ("ix_sales_contract_customer_id", "ix_sales_contract_contact_id"),
    ("ix_sales_deal_customer_id", "ix_sales_deal_contact_id"),
    ("ix_sales_offer_customer_id", "ix_sales_offer_contact_id"),
)

# (table, column, comment after, comment before) — the owner comments that name the context
_COMMENTS = (
    (
        "sales_contract",
        "contact_id",
        "Owned by the contact context. No DB-level FK.",
        "Owned by the customer context. No DB-level FK.",
    ),
    (
        "sales_offer",
        "contact_id",
        "Owned by the contact context. No DB-level FK.",
        "Owned by the customer context. No DB-level FK.",
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
