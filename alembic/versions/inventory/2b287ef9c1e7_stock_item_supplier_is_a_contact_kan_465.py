"""A stock item's supplier is a contact (KAN-465)

PRD-Stock: `supplier` is a reference + label. Anto, 2026-11-21, option A:
every new purchase booking names a contact with the supplier role; a booking
from before keeps the name typed then as its label, with no contact link.

- `supplier_name` becomes `supplier_label` (renamed in place: each booked
  name is kept as it was typed, and nothing is linked — a migration never
  reads another context's tables, and Anto ruled out guessing a contact).
- `supplier_contact_id` — the customer context's Customer.id, no FK (rule 2).
- `supplier_label_refreshed_at` — when the label was last read from the
  contact; NULL on a label typed before KAN-465.

DDL only, no data step. No index on `supplier_contact_id`: it is read by the
nightly label refresh (a full scan in id order, as every label job) and by a
contact merge, which is rare.

Revision ID: 2b287ef9c1e7
Revises: 6975a1f4524e
Create Date: 2026-11-24 04:10:41.202644

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2b287ef9c1e7'
down_revision: str | Sequence[str] | None = '6975a1f4524e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("stock_item", "supplier_name", new_column_name="supplier_label")
    op.add_column(
        "stock_item",
        sa.Column(
            "supplier_contact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the customer context (Customer.id, a contact with the supplier role). No DB-level FK.",
        ),
    )
    op.add_column("stock_item", sa.Column("supplier_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_item", "supplier_label_refreshed_at")
    op.drop_column("stock_item", "supplier_contact_id")
    op.alter_column("stock_item", "supplier_label", new_column_name="supplier_name")
