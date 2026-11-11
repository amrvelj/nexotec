"""sales_offer / sales_contract: index the configuration columns (KAN-563)

`sales.public.referenced_configuration_ids` is the safety net vehicle's daily
`vehicle.configurations.purge_unattached` asks before it deletes a
configuration no host took (KAN-270, ADR-120): one SELECT per slot column,
`configuration_id` and `trade_in_configuration_id` of offers and contracts,
filtered by `IN (…)` on a batch of ids. None of the four had an index, so
each batch scanned both tables. Decided as a technical question (ADR-126,
board clean-up 2026-11-11): index them now.

Plain `CREATE INDEX` inside the upgrade's one transaction (the KAN-92 advisory
lock forbids `CONCURRENTLY`, KAN-179): it holds a SHARE lock on each table
while it builds, which at today's sizes is a moment. DDL only — no data step,
so the offline render is unaffected. Downgrade drops the four indexes.

Revision ID: a5d3f1c7e9b2
Revises: c4f8a2d6e1b3
Create Date: 2026-11-11 18:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5d3f1c7e9b2"
down_revision: str | Sequence[str] | None = "c4f8a2d6e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_sales_offer_configuration_id", "sales_offer", "configuration_id"),
    ("ix_sales_offer_trade_in_configuration_id", "sales_offer", "trade_in_configuration_id"),
    ("ix_sales_contract_configuration_id", "sales_contract", "configuration_id"),
    ("ix_sales_contract_trade_in_configuration_id", "sales_contract", "trade_in_configuration_id"),
)


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
