"""sales_contract.legacy_transaction_id (KAN-26, WP-8 PR-7, ADR-050)

Provenance + idempotency key for scripts/migrate_transaction_rows.py,
same posture as vehicle_mdm.migrated_from_legacy_vehicle_id (WP-5 PR-7):
never used for lookups by the live application, purely an audit trail
and a "this transaction row was already migrated" guard so a re-run of
the migration script is a no-op rather than a duplicate.

No DB-level FK to `transaction.id` — transaction stays read-only/never-
dropped (ADR-050) but this repo's own convention (rule 2, CLAUDE.md) is
no cross-context foreign keys; sales_contract is a different context from
transaction's own historical shell scope, so a plain GUID column is the
right shape here too. Indexed, not unique — same posture as vehicle_mdm.
migrated_from_legacy_vehicle_id, which isn't unique either.

Revision ID: 50fe6834bfe2
Revises: d9e4c2a71f3b
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '50fe6834bfe2'
down_revision: Union[str, Sequence[str], None] = 'd9e4c2a71f3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_contract",
        sa.Column("legacy_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_sales_contract_legacy_transaction_id",
        "sales_contract",
        ["legacy_transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_contract_legacy_transaction_id", table_name="sales_contract")
    op.drop_column("sales_contract", "legacy_transaction_id")
