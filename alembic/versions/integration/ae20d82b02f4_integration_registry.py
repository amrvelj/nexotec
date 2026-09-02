"""integration_provider + integration_connection + integration_secret_ref
+ integration_entitlement + integration_call_log (WP-6 PR-1, Integrations
& API Credentials v0.1)

Revision ID: ae20d82b02f4
Revises: 915604f2f465
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ae20d82b02f4'
down_revision: Union[str, Sequence[str], None] = '915604f2f465'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_provider",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("required_secret_slots", sa.JSON(), nullable=False),
        sa.Column("capability_codes", sa.JSON(), nullable=False),
        sa.Column("docs_url", sa.String(length=500), nullable=True),
        sa.Column("supports_sandbox", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_provider_provider_code", "integration_provider", ["provider_code"], unique=True)

    op.create_table(
        "integration_connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integration_provider.id"), nullable=False
        ),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            # Matches app.integration.models.connection.IntegrationConnection's
            # own CheckConstraint exactly — SAEnum(native_enum=False)
            # stores the Python member NAME (uppercase), never .value.
            "(scope = 'PLATFORM' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL)",
            name="ck_integration_connection_scope_tenant_id",
        ),
    )
    op.create_index("ix_integration_connection_provider_id", "integration_connection", ["provider_id"])
    op.create_index("ix_integration_connection_tenant_id", "integration_connection", ["tenant_id"])
    op.create_index(
        "uq_integration_connection_tenant_provider_env",
        "integration_connection",
        ["tenant_id", "provider_id", "environment"],
        unique=True,
    )

    op.create_table(
        "integration_secret_ref",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integration_connection.id"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("secret_ref", sa.String(length=300), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_secret_ref_connection_id", "integration_secret_ref", ["connection_id"])
    op.create_index(
        "uq_integration_secret_ref_connection_slot", "integration_secret_ref", ["connection_id", "slot"], unique=True
    )

    op.create_table(
        "integration_entitlement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integration_connection.id"),
            nullable=False,
        ),
        sa.Column("capability_code", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_entitlement_connection_id", "integration_entitlement", ["connection_id"])
    op.create_index(
        "uq_integration_entitlement_connection_capability",
        "integration_entitlement",
        ["connection_id", "capability_code"],
        unique=True,
    )

    op.create_table(
        "integration_call_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integration_connection.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("cost_units", sa.DECIMAL(precision=12, scale=4), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_call_log_connection_id", "integration_call_log", ["connection_id"])
    op.create_index("ix_integration_call_log_tenant_id", "integration_call_log", ["tenant_id"])
    op.create_index("ix_integration_call_log_correlation_id", "integration_call_log", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("integration_call_log")
    op.drop_table("integration_entitlement")
    op.drop_table("integration_secret_ref")
    op.drop_table("integration_connection")
    op.drop_table("integration_provider")
