"""KAN-10 (C-F) — offers and contracts reference a configuration.

`configuration_id` (+ label, refreshed-at) is offer Path B's `build`
configuration (FR-C-12); `trade_in_configuration_id` (+ refreshed-at,
`trade_in_label` being its label) is a trade-in captured through the
valuation path in `record` mode. All nullable, no backfill: no existing
offer or contract was ever built in the configurator. Three-column
pattern, no FK into the vehicle context (rule 2).

Revision ID: d652c2e3627a
Revises: 6d2a8f4c1e95
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d652c2e3627a"
down_revision: Union[str, Sequence[str], None] = "6d2a8f4c1e95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("sales_offer", "sales_contract")
_OWNER = "Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK."


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("configuration_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_OWNER))
        op.add_column(table, sa.Column("configuration_label", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("configuration_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(
            table, sa.Column("trade_in_configuration_id", postgresql.UUID(as_uuid=True), nullable=True, comment=_OWNER)
        )
        op.add_column(
            table, sa.Column("trade_in_configuration_label_refreshed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "trade_in_configuration_label_refreshed_at")
        op.drop_column(table, "trade_in_configuration_id")
        op.drop_column(table, "configuration_label_refreshed_at")
        op.drop_column(table, "configuration_label")
        op.drop_column(table, "configuration_id")
