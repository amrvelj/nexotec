"""stock VIN unique only among items in stock (KAN-111)

A VIN may be in a dealership's stock once, never twice — but only among
items that have not left stock. A car the dealership invoiced years ago
(left_stock_at set, FR-I-12) keeps its VIN on its old row, so the index
over every row meant the same car coming back as a trade-in could never
be taken into stock again (Anto, 2026-10-07: it comes back as a new stock
item; the sold row stays as history).

- The old index is `uq_stock_item_tenant_vin`, the name 9cca34a11074 gave
  it; the model called it `uq_stock_item_tenant_id_vin`, a name that only
  ever existed in test databases built from the models. Model and database
  now share the new name.
- A plain CREATE INDEX, not CONCURRENTLY: it runs inside the startup
  migration transaction and keeps KAN-92's advisory lock. stock_item holds
  one row per car a dealership has stocked, so the build is short; writes
  to the table wait for it.
- Every row the old index accepted, the new one accepts (its predicate
  only narrows), so the upgrade cannot fail on existing data.

Revision ID: 3860e2a19b11
Revises: 5dd0453430a6
Create Date: 2026-10-07 17:52:51.977567

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3860e2a19b11"
down_revision: Union[str, Sequence[str], None] = "5dd0453430a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_stock_item_tenant_vin", table_name="stock_item")
    op.create_index(
        "uq_stock_item_tenant_id_vin_in_stock",
        "stock_item",
        ["tenant_id", "vin"],
        unique=True,
        postgresql_where=sa.text("vin IS NOT NULL AND left_stock_at IS NULL"),
    )


def downgrade() -> None:
    # The old rule — one row per VIN, sold rows included — cannot hold once
    # a sold car has come back into stock. Refuse with the reason instead of
    # failing on the index build halfway through the downgrade.
    returned = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT 1 FROM stock_item WHERE vin IS NOT NULL"
            " GROUP BY tenant_id, vin HAVING count(*) > 1) AS returned_cars"
        )
    )
    if returned:
        raise RuntimeError(
            f"Cannot downgrade past KAN-111: {returned} VIN(s) are on more than one stock item"
            " (a sold car taken into stock again), which the old index forbids."
        )
    op.drop_index("uq_stock_item_tenant_id_vin_in_stock", table_name="stock_item")
    op.create_index(
        "uq_stock_item_tenant_vin",
        "stock_item",
        ["tenant_id", "vin"],
        unique=True,
        postgresql_where=sa.text("vin IS NOT NULL"),
    )
