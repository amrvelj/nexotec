"""A stock item's location carries its name beside its id (KAN-739)

`stock_item.location_id` (platform's Location.id) had no label beside it, so
rule 2's three-column pattern was incomplete and the Stock grid could not
show «Standort» (ADR-060). Two nullable columns, no default, no data step:
a migration never reads another context's table (rule 2), so the rows
written before are filled by `scripts/backfill_stock_item_location_labels.py`
(through `app.platform.public.get_location_labels`), and from then on the
write path and the nightly `inventory.location_labels` keep them.

Revision ID: 6975a1f4524e
Revises: b7180a3c9d14
Create Date: 2026-11-16 04:18:20.789126

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6975a1f4524e'
down_revision: Union[str, Sequence[str], None] = 'b7180a3c9d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_item", sa.Column("location_label", sa.String(length=200), nullable=True))
    op.add_column("stock_item", sa.Column("location_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_item", "location_label_refreshed_at")
    op.drop_column("stock_item", "location_label")
