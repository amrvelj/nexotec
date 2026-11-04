"""sales_contract.invoice_requested_at: «Rechnung anfordern» once per contract (KAN-539)

Until now `request_invoice` stored nothing on the contract, so a second
request passed every gate and published a second
`sales.contract.invoice_requested`. Anto (2026-11-03, option A): once per
contract — the request stamps this column, a second one is refused (409
`invoice_already_requested`) and the screen disables the action.

Backfill: a contract that already requested invoicing carries an
`invoice_requested` row in the append-only audit log (written in the same
transaction as the event since KAN-523); its earliest such row's time is
the stamp, so a contract requested before this migration cannot be
requested again either. Every other contract stays NULL. Guarded for
offline mode (KAN-174): the backfill reads rows.

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


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute(
            "-- KAN-539 invoice_requested_at backfill skipped: it reads rows and needs a live database. "
            "Contracts that requested invoicing before this migration could then request it once more."
        )
        return
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE sales_contract c SET invoice_requested_at = a.first_requested_at "
            "FROM (SELECT entity_id, min(created_at) AS first_requested_at FROM audit_event "
            "WHERE entity_type = 'sales_contract' AND action = 'invoice_requested' GROUP BY entity_id) a "
            "WHERE a.entity_id = c.id AND c.invoice_requested_at IS NULL"
        )
    )
    stamped = conn.execute(
        sa.text("SELECT count(*) FROM sales_contract WHERE invoice_requested_at IS NOT NULL")
    ).scalar()
    print(f"KAN-539 backfill: {stamped} contract(s) had already requested invoicing")


def upgrade() -> None:
    op.add_column("sales_contract", sa.Column("invoice_requested_at", sa.DateTime(timezone=True), nullable=True))
    _backfill()


def downgrade() -> None:
    op.drop_column("sales_contract", "invoice_requested_at")
