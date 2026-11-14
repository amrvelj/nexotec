"""KAN-718: stock_item_publishing.at_marketplace — what the marketplace holds

`transmission_status` is the last attempt's outcome, not a history, and an
unpublish never wrote it at all: an unpublish whose empty feed was never
delivered (the connection disabled, a failed delivery) looked like a clean
one. `at_marketplace` records whether the last feed the marketplace accepted
holds the listing.

Backfill. A published row whose last transmission reached the marketplace
(`transmitted`) is held. An unpublished `transmitted` row cannot tell a clean
unpublish from one whose feed never arrived (the old unpublish wrote no
outcome), so it counts as held unless its channel delivered a feed after it
was unpublished: a published row of the same Betrieb and channel that reads
`transmitted` with a `last_attempted_at` later than the unpublished row's
`updated_at` (the unpublish's own write; nothing else touched such a row).
That later feed omitted it, so the marketplace no longer holds it.

Cost of what remains held: an unpublish that no later delivered feed
covers — the channel's last listing unpublished, or the connection disabled
since — shows in inventory's nightly report and makes offboarding send the
channel its empty feed; with no enabled connection left, offboarding's
withdrawal is blocked until staff enable one once. Reading such a row as gone
would hide exactly the listings this ticket is about.

Not covered: a row whose last attempt FAILED while an earlier feed of it is
still live (the ticket's second case, already in that state before this
migration). It reads `false` — counting every FAILED row as held would also
count listings never delivered at all; that trade is Anto's (KAN-718
comment). Rows written after this migration record the case themselves.

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
    op.execute(
        """
        UPDATE stock_item_publishing AS row
        SET at_marketplace = true
        WHERE row.transmission_status = 'transmitted'
          AND (
            row.state = 'published'
            OR NOT EXISTS (
              SELECT 1 FROM stock_item_publishing AS later
              WHERE later.tenant_id = row.tenant_id
                AND later.channel = row.channel
                AND later.state = 'published'
                AND later.transmission_status = 'transmitted'
                AND later.last_attempted_at > row.updated_at
            )
          )
        """
    )


def downgrade() -> None:
    op.drop_column("stock_item_publishing", "at_marketplace")
