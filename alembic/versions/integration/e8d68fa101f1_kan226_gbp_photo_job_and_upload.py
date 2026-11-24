"""KAN-226 / KAN-784 (D-A-32, FR-A-35; ADR-209): the Business Profile photo upload.

- `integration_gbp_photo_job`: one row per binding waiting for a photo pass —
  when it is due, how many attempts failed, which queuing it is, when it was dead-lettered and a
  closed code for the last failure.
- `integration_gbp_photo_upload`: one row per photo uploaded to a binding's
  Google location under one category, with Google's media name — or
  Google's refusal of it, as a closed code.
Ids, codes and Google's resource names only; no image bytes and no secret
material, so the downgrade drops both.

Revision ID: e8d68fa101f1
Revises: a4e81c9d3f27
Create Date: 2026-11-24 06:30:17.633753

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "e8d68fa101f1"
down_revision: str | Sequence[str] | None = "a4e81c9d3f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_gbp_photo_job",
        sa.Column("binding_id", GUID(), nullable=False),
        sa.Column("due_at", UTCDateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("dead_at", UTCDateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["integration_gbp_location_binding.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", name="uq_integration_gbp_photo_job_binding"),
    )
    op.create_index(op.f("ix_integration_gbp_photo_job_due_at"), "integration_gbp_photo_job", ["due_at"], unique=False)
    op.create_index(
        op.f("ix_integration_gbp_photo_job_tenant_id"), "integration_gbp_photo_job", ["tenant_id"], unique=False
    )
    op.create_table(
        "integration_gbp_photo_upload",
        sa.Column("binding_id", GUID(), nullable=False),
        sa.Column(
            "asset_id",
            GUID(),
            nullable=False,
            comment="Owned by the platform context (BrandingAsset.id). No DB-level FK.",
        ),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("google_media_name", sa.String(length=300), nullable=True),
        sa.Column("refusal_code", sa.String(length=64), nullable=True),
        sa.Column("uploaded_at", UTCDateTime(), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["integration_gbp_location_binding.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "asset_id", "category", name="uq_integration_gbp_photo_upload_asset"),
        sa.CheckConstraint(
            "(google_media_name IS NULL) <> (refusal_code IS NULL)", name="ck_integration_gbp_photo_upload_outcome"
        ),
    )
    op.create_index(
        op.f("ix_integration_gbp_photo_upload_tenant_id"), "integration_gbp_photo_upload", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_gbp_photo_upload_tenant_id"), table_name="integration_gbp_photo_upload")
    op.drop_table("integration_gbp_photo_upload")
    op.drop_index(op.f("ix_integration_gbp_photo_job_tenant_id"), table_name="integration_gbp_photo_job")
    op.drop_index(op.f("ix_integration_gbp_photo_job_due_at"), table_name="integration_gbp_photo_job")
    op.drop_table("integration_gbp_photo_job")
