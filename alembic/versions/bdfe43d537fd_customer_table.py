"""customer table

Revision ID: bdfe43d537fd
Revises: d176da890cdf
Create Date: 2026-08-06 05:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bdfe43d537fd'
down_revision: Union[str, Sequence[str], None] = 'd176da890cdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealer.id", name="fk_customer_tenant_id_dealer"),
            nullable=False,
        ),
        sa.Column(
            "customer_type",
            sa.Enum("individual", name="customer_type", native_enum=False, length=32),
            nullable=False,
            server_default="individual",
        ),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("address_street", sa.String(length=200), nullable=True),
        sa.Column("address_house_number", sa.String(length=20), nullable=True),
        sa.Column("address_postal_code", sa.String(length=4), nullable=True),
        sa.Column("address_locality", sa.String(length=100), nullable=True),
        sa.Column("address_canton", sa.String(length=2), nullable=True),
        sa.Column("address_country", sa.String(length=2), nullable=True),
        sa.Column(
            "preferred_contact_method",
            sa.Enum("email", "phone", "sms", name="preferred_contact_method", native_enum=False, length=16),
            nullable=True,
        ),
        sa.Column(
            "lifecycle_status",
            sa.Enum(
                "prospect",
                "active",
                "inactive",
                "merged",
                "do_not_contact",
                name="customer_lifecycle_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="prospect",
        ),
        sa.Column(
            "source",
            sa.Enum(
                "walk_in", "phone", "web_lead", "marketplace", "other",
                name="customer_source", native_enum=False, length=32,
            ),
            nullable=True,
        ),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "duplicate_of_customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer.id", name="fk_customer_duplicate_of_customer_id_customer"),
            nullable=True,
        ),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_customer_tenant_id", "customer", ["tenant_id"])
    op.create_unique_constraint("uq_customer_tenant_id_email", "customer", ["tenant_id", "email"])


def downgrade() -> None:
    op.drop_constraint("uq_customer_tenant_id_email", "customer", type_="unique")
    op.drop_index("ix_customer_tenant_id", table_name="customer")
    op.drop_table("customer")
