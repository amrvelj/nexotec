"""KAN-555: the legacy Sales `transaction` table is dropped

Retired for writes since ADR-050 (`sales_offer` / `sales_contract` replaced
it); its import and its readers went with KAN-79 and this change. ADR-117:
retire it. Anto, 2026-11-13, option A: «drop every legacy `transaction` row
with the table — drafts, cancelled and any never-migrated completed row (no
productive data yet)», with no refusal guard.

**Data.** Every row goes with the table. The upgrade first logs how many it
drops, by type and status, on the `alembic` logger (INFO), so a deploy log
shows what went. A contract migrated from a legacy sale keeps its
`legacy_transaction_id` (a plain GUID, now naming nothing) and with it the
`legacy_sale` refusal: the old system invoiced it. No table holds a foreign key
to `transaction` (PR-2 dropped them); the vehicle chain's own revision drops
the one column that named it (`vehicle_mdm_custody_event.transaction_id`).

**Offline (`--sql`).** The count reads rows, so it renders as a comment; the
DROP renders as usual. Offline mode is read from the migration context
(`as_sql`, what `context.is_offline_mode()` reports), so the steps also run
when a test calls them outside an Alembic environment.

**Downgrade.** Recreates the table empty, exactly as it stood at
`976ea5f43dc0` (columns, defaults, the two enum CHECKs, the primary key, the
four indexes, the table and column comments), literals only — the model is
gone (KAN-517). The data is not restored.
"""

import logging
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "4726dcd89260"
down_revision: str | Sequence[str] | None = "976ea5f43dc0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    if op.get_context().as_sql:
        op.execute("-- KAN-555: the count of dropped transaction rows is skipped: it reads rows and needs a live database")
    else:
        counts = op.get_bind().execute(
            text('SELECT transaction_type, status, count(*) FROM "transaction" GROUP BY 1, 2 ORDER BY 1, 2')
        ).all()
        total = sum(n for _type, _status, n in counts)
        logger.info("KAN-555: dropping the legacy transaction table with %d row(s)", total)
        for transaction_type, status, n in counts:
            logger.info("KAN-555:   %s / %s: %d", transaction_type, status, n)
    op.drop_table("transaction")


_CREATE_TRANSACTION = """
CREATE TABLE "transaction" (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    transaction_type character varying(16) NOT NULL,
    status character varying(16) DEFAULT 'draft' NOT NULL,
    customer_id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    primary_user_id uuid NOT NULL,
    amount numeric(12, 2),
    transaction_date timestamp with time zone,
    external_ref character varying(255),
    notes text,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    created_by uuid,
    updated_by uuid,
    CONSTRAINT transaction_pkey PRIMARY KEY (id),
    CONSTRAINT ck_transaction_status_enum CHECK (status IN ('draft', 'completed', 'cancelled')),
    CONSTRAINT ck_transaction_transaction_type_enum CHECK (transaction_type IN ('sale', 'trade_in'))
)
"""

_TABLE_COMMENT = (
    "RETIRED (WP-8 PR-7, ADR-050/S-D12): superseded by sales_offer/sales_contract. "
    "Read-only from this point on — new writes are refused at the service layer "
    "(app.sales.services.transaction). Never dropped."
)

_COLUMN_COMMENTS = {
    "tenant_id": "Owned by the platform context (Dealership). No DB-level FK.",
    "customer_id": "Owned by the customer context. No DB-level FK (PR-2, ADR-015).",
    "vehicle_id": "Owned by the vehicle context. No DB-level FK (PR-2, ADR-015).",
    "primary_user_id": "Owned by the platform context (User). No DB-level FK.",
}


def downgrade() -> None:
    op.execute(_CREATE_TRANSACTION)
    for column in ("tenant_id", "customer_id", "vehicle_id", "primary_user_id"):
        op.create_index(f"ix_transaction_{column}", "transaction", [column])
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite has no table or column comments.
    op.execute(f"""COMMENT ON TABLE "transaction" IS '{_TABLE_COMMENT.replace("'", "''")}'""")
    for column, comment in _COLUMN_COMMENTS.items():
        op.execute(f"""COMMENT ON COLUMN "transaction".{column} IS '{comment.replace("'", "''")}'""")
