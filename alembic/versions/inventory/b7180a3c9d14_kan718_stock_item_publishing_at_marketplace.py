"""KAN-718: stock_item_publishing.at_marketplace — what the marketplace holds

`transmission_status` is the last attempt's outcome, not a history, and an
unpublish never wrote it at all: an unpublish whose empty feed was never
delivered (the connection disabled, a failed delivery) looked like a clean
one. `at_marketplace` records whether the last feed the marketplace accepted
holds the listing.

Backfill, conservative: every row whose last transmission reached the
marketplace (`transmitted`) — published or not — counts as at the
marketplace. An unpublished row cannot tell a clean unpublish from one whose
feed never arrived (the unpublish path wrote no outcome); reading it as
still live makes inventory's reconciliation report it and offboarding send
its channel the empty feed, and the next delivered feed of its channel
clears it. Reading it as gone would hide exactly the listings this ticket is
about.

Not covered: a row whose last attempt FAILED while an earlier feed of it is
still live (the ticket's second case, already in that state before this
migration). It reads `false` — counting every FAILED row as held would also
count listings never delivered at all, which could block an offboarding
with no connection left; that trade is Anto's (KAN-718 comment). Rows
written after this migration record the case themselves.

Revision ID: b7180a3c9d14
Revises: 8e41c7a95b03
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7180a3c9d14"
down_revision: str | Sequence[str] | None = "8e41c7a95b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock_item_publishing",
        sa.Column("at_marketplace", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE stock_item_publishing SET at_marketplace = true WHERE transmission_status = 'transmitted'")


def downgrade() -> None:
    op.drop_column("stock_item_publishing", "at_marketplace")
