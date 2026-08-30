"""Create stock_item + stock_number_sequence (WP-7 PR-1, ADR-045, ADR-054)

lifecycle_status is a 3-value enum stored as a plain string column
(native_enum=False, matching every other context's SAEnum convention) —
"sold" is deliberately not among them (see app.inventory.models.stock_item's
own docstring); enforcing this at the DB level too, not just the Python
enum, is what tests/test_inventory_stock_item.py's own enum-shape assertion
checks against.

Revision ID: 9cca34a11074
Revises: f66cbebd2e2f
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9cca34a11074'
down_revision: Union[str, Sequence[str], None] = 'f66cbebd2e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_number_sequence",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )

    op.create_table(
        "stock_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column("stock_number", sa.String(length=16), nullable=False),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK.",
        ),
        sa.Column("vin", sa.String(length=17), nullable=True),
        sa.Column("vehicle_label", sa.String(length=200), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=16), nullable=False),
        sa.Column("reservation_state", sa.String(length=16), nullable=False),
        sa.Column("condition", sa.String(length=16), nullable=False),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the platform context (Location.id). No DB-level FK.",
        ),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("list_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("effective_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("first_registration_date", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_stock_item_tenant_id", "stock_item", ["tenant_id"])
    op.create_index("ix_stock_item_stock_number", "stock_item", ["stock_number"])
    op.create_index("ix_stock_item_vehicle_id", "stock_item", ["vehicle_id"])
    op.create_index("ix_stock_item_vin", "stock_item", ["vin"])
    op.create_index("ix_stock_item_tenant_lifecycle", "stock_item", ["tenant_id", "lifecycle_status"])
    op.create_unique_constraint("uq_stock_item_tenant_stock_number", "stock_item", ["tenant_id", "stock_number"])
    op.create_index(
        "uq_stock_item_tenant_vin",
        "stock_item",
        ["tenant_id", "vin"],
        unique=True,
        postgresql_where=sa.text("vin IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("stock_item")
    op.drop_table("stock_number_sequence")
