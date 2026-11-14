"""KAN-466: customer_contact_group — the contact groups a customer carries

Values of the platform reference list `contact_group` by code (no FK across
contexts). Empty for every customer after this migration.

Revision ID: fdf7c90750b7
Revises: e7a3c95b1d62
Create Date: 2026-11-13 23:49:58.982895

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fdf7c90750b7"
down_revision: str | Sequence[str] | None = "e7a3c95b1d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_contact_group",
        sa.Column(
            "group_id",
            app.core.types.GUID(),
            nullable=False,
            comment="Owned by the platform context (DealerGroup). No DB-level FK.",
        ),
        sa.Column("customer_id", app.core.types.GUID(), nullable=False),
        sa.Column(
            "value_code",
            sa.String(length=64),
            nullable=False,
            comment="Owned by the platform context (reference list contact_group, by value_code). No DB-level FK.",
        ),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customer.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "value_code", name="uq_customer_contact_group_customer_id_value_code"),
    )
    op.create_index(
        op.f("ix_customer_contact_group_customer_id"), "customer_contact_group", ["customer_id"], unique=False
    )
    op.create_index(op.f("ix_customer_contact_group_group_id"), "customer_contact_group", ["group_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_customer_contact_group_group_id"), table_name="customer_contact_group")
    op.drop_index(op.f("ix_customer_contact_group_customer_id"), table_name="customer_contact_group")
    op.drop_table("customer_contact_group")
