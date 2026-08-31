"""Retire `transaction` (WP-8 PR-7, S-D12/ADR-050) — comment-only,
never dropped.

sales_offer/sales_contract supersede it. `transaction` goes READ-ONLY at
this cutover — the row stays, its own migration
(e5873926e0a9_transaction_table.py, pre-ADR-015 frozen trunk) is
untouched, and scripts/seed_migration_smoke_test.py's own Transaction
seeding keeps working unmodified, which is the whole point of a
comment-only migration rather than DDL against the data or a trigger (a
trigger is not portable to the SQLite fast lane either). The service/API
layer (app/sales/services/transaction.py, app/sales/api/transactions.py)
is what actually refuses new writes — this migration only documents the
retirement at the schema level, for anyone reading the schema directly.

Revision ID: 6aa677b9a9cb
Revises: c2e190e72877
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6aa677b9a9cb'
down_revision: Union[str, Sequence[str], None] = 'c2e190e72877'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMENT = (
    "RETIRED (WP-8 PR-7, ADR-050/S-D12): superseded by sales_offer/sales_contract. "
    "Read-only from this point on — new writes are refused at the service layer "
    "(app.sales.services.transaction). Never dropped."
)


def upgrade() -> None:
    # SQLite has no native COMMENT ON TABLE — a no-op there (the fast
    # pre-commit lane, ADR-011), a real, durable comment on Postgres (the
    # test database of record and the only lane that gates a merge).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"COMMENT ON TABLE transaction IS '{_COMMENT}'")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("COMMENT ON TABLE transaction IS NULL")
