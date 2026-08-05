"""dealer and user tables

Revision ID: d176da890cdf
Revises: c0a737bad71f
Create Date: 2026-08-05 21:08:45.273610

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd176da890cdf'
down_revision: Union[str, Sequence[str], None] = 'c0a737bad71f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dealer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("dba_name", sa.String(length=200), nullable=True),
        sa.Column("dealer_license_number", sa.String(length=64), nullable=False),
        sa.Column("license_state", sa.String(length=2), nullable=False),
        sa.Column(
            "franchise_type",
            sa.Enum("franchise", "independent", name="franchise_type", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("oem_affiliations", postgresql.JSON, nullable=True),
        sa.Column("address_street", sa.String(length=200), nullable=False),
        sa.Column("address_house_number", sa.String(length=20), nullable=False),
        sa.Column("address_postal_code", sa.String(length=4), nullable=False),
        sa.Column("address_locality", sa.String(length=100), nullable=False),
        sa.Column("address_canton", sa.String(length=2), nullable=False),
        sa.Column("address_country", sa.String(length=2), nullable=False, server_default="CH"),
        sa.Column("phone", sa.String(length=20), nullable=False),
        # Encrypted at rest at the application layer (EncryptedString) — the
        # column itself is just opaque ciphertext text, no dialect-specific
        # encryption feature used.
        sa.Column("tax_id", sa.Text(), nullable=False),
        sa.Column("parent_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending_onboarding",
                "active",
                "suspended",
                "offboarded",
                name="dealer_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="pending_onboarding",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealer.id", name="fk_user_tenant_id_dealer"),
            nullable=False,
        ),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "sales",
                "service_advisor",
                "finance_manager",
                "gm",
                "admin",
                "technician",
                "parts",
                "other",
                name="user_role",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "access_role",
            sa.Enum(
                "platform_admin",
                "dealer_admin",
                "sales",
                "inventory",
                "auditor",
                name="access_role",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "employment_status",
            sa.Enum(
                "active", "on_leave", "terminated", name="employment_status", native_enum=False, length=32
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("auth_identity_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "invited",
                "active",
                "suspended",
                "deactivated",
                name="user_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="invited",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_user_tenant_id", "user", ["tenant_id"])
    op.create_unique_constraint("uq_user_email", "user", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_user_email", "user", type_="unique")
    op.drop_index("ix_user_tenant_id", table_name="user")
    op.drop_table("user")
    op.drop_table("dealer")
