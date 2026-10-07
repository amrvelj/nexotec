"""KAN-10 (C-F) — a pipeline stock item references its configuration
(FR-C-12, FR-C-13). Nullable, no backfill: no existing stock item was
created from the configurator. Three-column pattern, no FK into the vehicle
context (rule 2).

Revision ID: 16f8f5791a1b
Revises: a7c3e91d2b40
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "16f8f5791a1b"
down_revision: Union[str, Sequence[str], None] = "a7c3e91d2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_item",
        sa.Column(
            "configuration_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK.",
        ),
    )
    op.add_column("stock_item", sa.Column("configuration_label", sa.String(200), nullable=True))
    op.add_column("stock_item", sa.Column("configuration_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_stock_item_configuration_id", "stock_item", ["configuration_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_item_configuration_id", table_name="stock_item")
    op.drop_column("stock_item", "configuration_label_refreshed_at")
    op.drop_column("stock_item", "configuration_label")
    op.drop_column("stock_item", "configuration_id")
