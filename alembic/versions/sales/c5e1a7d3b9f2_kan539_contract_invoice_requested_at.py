"""sales_contract.invoice_requested_at: «Rechnung anfordern» once per contract (KAN-539)

Until now `request_invoice` stored nothing on the contract, so a second
request passed every gate and published a second
`sales.contract.invoice_requested`. Anto (2026-11-03, option A): once per
contract — the request stamps this column, a second one is refused (409
`invoice_already_requested`) and the screen disables the action.

Backfill: a contract that already requested invoicing published
`sales.contract.invoice_requested`, and the outbox keeps every event (never
purged; the KAN-100 replica backfill reads it the same way). Since KAN-523
the request also writes an `invoice_requested` audit row. The stamp is the
earliest of the two, so every contract requested before this migration —
before KAN-523 too — is refused a second request. Every other contract stays
NULL. Anto confirmed this backfill (2026-11-04, option A; his plan had
left existing rows empty). Re-runnable (it fills only NULL stamps). Guarded for offline mode
(KAN-174): the backfill reads rows.

Downgrade drops the column; a second request is then possible again, so it
is for a failed deploy only.

Revision ID: c5e1a7d3b9f2
Revises: a4c9e2d7f1b3
Create Date: 2026-11-04 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c5e1a7d3b9f2"
down_revision: str | Sequence[str] | None = "a4c9e2d7f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL = sa.text(
    "UPDATE sales_contract c SET invoice_requested_at = r.first_requested_at "
    "FROM (SELECT contract_id, min(requested_at) AS first_requested_at FROM ("
    "SELECT aggregate_id AS contract_id, occurred_at AS requested_at FROM outbox_message "
    "WHERE event_type = 'sales.contract.invoice_requested' AND aggregate_type = 'sales_contract' "
    "UNION ALL "
    "SELECT entity_id, created_at FROM audit_event "
    "WHERE entity_type = 'sales_contract' AND action = 'invoice_requested'"
    ") requests GROUP BY contract_id) r "
    "WHERE r.contract_id = c.id AND c.invoice_requested_at IS NULL"
)


def backfill(conn: sa.engine.Connection) -> int:
    """Stamps every contract whose invoicing was requested before this
    migration; returns how many contracts carry a stamp afterwards."""

    conn.execute(_BACKFILL)
    stamped = conn.execute(sa.text("SELECT count(*) FROM sales_contract WHERE invoice_requested_at IS NOT NULL"))
    return int(stamped.scalar() or 0)


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute(
            "-- KAN-539 invoice_requested_at backfill skipped: it reads rows and needs a live database. "
            "Contracts that requested invoicing before this migration could then request it once more."
        )
        return
    stamped = backfill(op.get_bind())
    print(f"KAN-539 backfill: {stamped} contract(s) had already requested invoicing")


def upgrade() -> None:
    op.add_column("sales_contract", sa.Column("invoice_requested_at", sa.DateTime(timezone=True), nullable=True))
    _backfill()


def downgrade() -> None:
    op.drop_column("sales_contract", "invoice_requested_at")
