"""KAN-464 PR-3: the renamed consumers keep the events they processed

Customers became contacts (ADR-210). Six consumers of other contexts' events
were named `customer.*` and are `contact.*` now; their events did not change
name. Idempotency is keyed by (event, consumer name), so a row still pending
or dead in the outbox that the old name already processed would run again
under the new one. Each old name's `processed_event` rows are copied to the
new name; the old rows stay (append-only history, and what a downgrade's code
reads). `sales.customer_merged` is not here: it keeps its name, subscribed to
`customer.merged` beside the new `sales.contact_merged` (KAN-464 PR-5 retires
it).

Deploy order: the worker is stopped before the upgrade and started on the new
code after it, so no event is processed under the old name once copied.

Plain SQL with literal names: it renders under `--sql` as it runs.

Revision ID: 5e1c7a9d3b20
Revises: f2b6d9c4a1e3
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e1c7a9d3b20"
down_revision: str | Sequence[str] | None = "f2b6d9c4a1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (old consumer name, new consumer name) — written out: a migration keeps the
# names it ran with (ADR-133).
_RENAMED = (
    ("customer.stock_item_bookings_on_purchased", "contact.stock_item_bookings_on_purchased"),
    ("customer.stock_item_bookings_on_storno", "contact.stock_item_bookings_on_storno"),
    ("customer.trade_in_purchased", "contact.trade_in_purchased"),
    ("customer.trade_in_storno", "contact.trade_in_storno"),
    ("customer.vehicle_party_labels_on_summary_changed", "contact.vehicle_party_labels_on_summary_changed"),
    ("customer.vehicle_parties_on_vehicle_merged", "contact.vehicle_parties_on_vehicle_merged"),
)


def _copy(source: str, target: str) -> None:
    op.execute(
        "INSERT INTO processed_event (event_id, consumer_name, processed_at)"
        f" SELECT event_id, '{target}', processed_at FROM processed_event WHERE consumer_name = '{source}'"
        " ON CONFLICT DO NOTHING"
    )


def upgrade() -> None:
    for old, new in _RENAMED:
        _copy(old, new)


def downgrade() -> None:
    # What the new names processed since goes back to the old names, so the
    # downgraded code does not process it again; then the new names' rows go.
    for old, new in _RENAMED:
        _copy(new, old)
        op.execute(f"DELETE FROM processed_event WHERE consumer_name = '{new}'")
