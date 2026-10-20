"""sales_offer / sales_contract / sales_deal: the responsible seller (KAN-200, FR-A-37, D-A-35)

Deactivating a user hands their open offers and pending contracts to a
colleague or to nobody (the sales list's unassigned queue). Until now the
only person on an offer was `created_by`, which history keeps (A-RULE-5)
and which cannot be handed over. Anto (2026-10-19): add a responsible
seller now, filled from the creator for existing records.

Three columns per table (rule 2, the three-column pattern — a platform
User id, its display label, when the label was read; no FK), indexed on
the id for the ownership and queue queries. `sales_deal` carries the live
entity's three so the list shows and filters without a join.

Backfill: `owner_user_id = created_by` where the creator is a user who
acts in the row's Betrieb — homed there, or holding an active membership
(a sister-Betrieb member creates offers there and owns them, as at
runtime) — and still holds or awaits access (`active` or `invited`, the
statuses `default_owner` admits); the label from the user row
(`first_name last_name`). A creator that names no user (a system write,
KAN-243), a platform staff member who is not a member, or a suspended or
deactivated creator leaves the owner NULL — the unassigned queue; nightly
reconciliation would otherwise report every open deal of a long-gone
seller on the first run. `sales_deal.owner_label` is indexed: the grid
sorts on it. The deal row takes its contract's seller when it
has one, else its offer's. Guarded for offline mode (KAN-174): the
backfill reads rows.

Downgrade drops the nine columns; who was responsible is then lost (the
creator stays), so it is for a failed deploy only.

Revision ID: a4c9e2d7f1b3
Revises: 1fe7be00026d
Create Date: 2026-10-19 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a4c9e2d7f1b3"
down_revision: str | Sequence[str] | None = "1fe7be00026d"
branch_labels: str | Sequence[str] | None = None
# The backfill reads platform's `user` and `dealership_membership.valid_to` (b7e4d2a9c153, KAN-199).
depends_on: str | Sequence[str] | None = ("b7e4d2a9c153",)

_OWNER_COMMENT = "Owned by the platform context (User). No DB-level FK."


_TABLES = ("sales_offer", "sales_contract", "sales_deal")


def _add_owner_columns(table: str) -> None:
    op.add_column(
        table,
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_OWNER_COMMENT),
    )
    op.add_column(table, sa.Column("owner_label", sa.String(length=200), nullable=True))
    op.add_column(table, sa.Column("owner_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"])


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute(
            "-- KAN-200 owner backfill skipped: it reads rows and needs a live database. "
            "Existing offers and contracts then have no responsible seller until one is set in the workspace."
        )
        return
    conn = op.get_bind()
    for table in ("sales_offer", "sales_contract"):
        conn.execute(
            sa.text(
                f'UPDATE "{table}" t SET owner_user_id = u.id, '
                "owner_label = trim(u.first_name || ' ' || u.last_name), owner_label_refreshed_at = now() "
                'FROM "user" u WHERE t.created_by = u.id AND t.owner_user_id IS NULL '
                "AND u.status IN ('active', 'invited') "
                "AND (u.tenant_id = t.tenant_id OR EXISTS ("
                "SELECT 1 FROM dealership_membership m WHERE m.user_id = u.id AND m.dealership_id = t.tenant_id "
                "AND (m.valid_to IS NULL OR m.valid_to > now())))"
            )
        )
        filled = conn.execute(sa.text(f'SELECT count(*) FROM "{table}" WHERE owner_user_id IS NOT NULL')).scalar()
        total = conn.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar()
        print(f"KAN-200 backfill: {table}: {filled} of {total} row(s) given their creator as responsible seller")
    conn.execute(
        sa.text(
            "UPDATE sales_deal d SET owner_user_id = c.owner_user_id, owner_label = c.owner_label, "
            "owner_label_refreshed_at = c.owner_label_refreshed_at FROM sales_contract c WHERE d.contract_id = c.id"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE sales_deal d SET owner_user_id = o.owner_user_id, owner_label = o.owner_label, "
            "owner_label_refreshed_at = o.owner_label_refreshed_at "
            "FROM sales_offer o WHERE d.contract_id IS NULL AND d.offer_id = o.id"
        )
    )


def upgrade() -> None:
    for table in _TABLES:
        _add_owner_columns(table)
    op.create_index("ix_sales_deal_owner_label", "sales_deal", ["owner_label"])
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_sales_deal_owner_label", table_name="sales_deal")
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table)
        op.drop_column(table, "owner_label_refreshed_at")
        op.drop_column(table, "owner_label")
        op.drop_column(table, "owner_user_id")
