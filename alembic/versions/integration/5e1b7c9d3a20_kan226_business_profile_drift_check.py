"""KAN-226 PR-4 (D-A-32, FR-A-35): the nightly Business Profile drift check.

- `integration_gbp_drift_check`: one row per binding — when its Google
  location was last read back, the outcome (in sync, drift, failed,
  skipped), the codes of the fields that differ, and a closed reason code.
Ids, field codes and reason codes only; no value from Google and no secret
material, so the downgrade drops it.

Revision ID: 5e1b7c9d3a20
Revises: 8c4f19b2d471
Create Date: 2026-11-22 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "5e1b7c9d3a20"
down_revision: str | Sequence[str] | None = "8c4f19b2d471"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_gbp_drift_check",
        sa.Column("binding_id", GUID(), nullable=False),
        sa.Column("checked_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("drifted_fields", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('in_sync', 'drift', 'failed', 'skipped')",
            name="ck_integration_gbp_drift_check_outcome_enum",
        ),
        sa.ForeignKeyConstraint(["binding_id"], ["integration_gbp_location_binding.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", name="uq_integration_gbp_drift_check_binding"),
    )
    op.create_index("ix_integration_gbp_drift_check_tenant_id", "integration_gbp_drift_check", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_gbp_drift_check_tenant_id", table_name="integration_gbp_drift_check")
    op.drop_table("integration_gbp_drift_check")
