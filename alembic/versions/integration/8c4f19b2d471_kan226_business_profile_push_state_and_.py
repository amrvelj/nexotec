"""KAN-226 PR-3 (D-A-32, FR-A-35): what the Business Profile push consumer
last sent to a binding's Google location.

- `integration_gbp_push_state`: one row per binding — the hash of the facts
  the last successful push read (an event whose facts hash the same sends
  nothing), when, which fields, and the departments Google's category offers
  no hours type for.
- `integration_gbp_photo`: one row per publishing image uploaded to a
  binding's Google location, so an image is uploaded once per binding.

Both hold ids and hashes only; no secret material, so the downgrade drops
them.

Revision ID: 8c4f19b2d471
Revises: c7d2a9e4f1b8
Create Date: 2026-11-21 14:10:43.168970

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "8c4f19b2d471"
down_revision: str | Sequence[str] | None = "c7d2a9e4f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "integration_gbp_photo",
        sa.Column("binding_id", GUID(), nullable=False),
        sa.Column(
            "image_id",
            GUID(),
            nullable=False,
            comment="Owned by the platform context (BrandingAsset.id). No DB-level FK.",
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("google_media_name", sa.String(length=512), nullable=False),
        sa.Column("uploaded_at", UTCDateTime(timezone=True), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["binding_id"], ["integration_gbp_location_binding.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "image_id", name="uq_integration_gbp_photo_binding_image"),
    )
    op.create_index("ix_integration_gbp_photo_binding_id", "integration_gbp_photo", ["binding_id"])
    op.create_index("ix_integration_gbp_photo_tenant_id", "integration_gbp_photo", ["tenant_id"])
    op.create_table(
        "integration_gbp_push_state",
        sa.Column("binding_id", GUID(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("pushed_at", UTCDateTime(timezone=True), nullable=True),
        sa.Column("pushed_fields", sa.JSON(), nullable=False),
        sa.Column("unmapped_departments", sa.JSON(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["binding_id"], ["integration_gbp_location_binding.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", name="uq_integration_gbp_push_state_binding"),
    )
    op.create_index("ix_integration_gbp_push_state_tenant_id", "integration_gbp_push_state", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_gbp_push_state_tenant_id", table_name="integration_gbp_push_state")
    op.drop_table("integration_gbp_push_state")
    op.drop_index("ix_integration_gbp_photo_tenant_id", table_name="integration_gbp_photo")
    op.drop_index("ix_integration_gbp_photo_binding_id", table_name="integration_gbp_photo")
    op.drop_table("integration_gbp_photo")
