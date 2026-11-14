"""KAN-218: `export_job` and the dealership's archive columns

Dealer Administration D-A-31, FR-A-44/45 (Anto, 2026-11-13: Q1 A seven-day
download, Q2 B staff-produced archive exports, Q3 A ten full years from the
offboarding date). `export_job` holds one row per export of a Betrieb's data —
the manager's own (`self_service`) or one platform staff produce from the
archive (`archive`, with a reason) — and the object-store key of its ZIP,
never the file. `dealership` gains `offboarded_at`, `retention_until` (the
deletion date), `erasure_progress` (the contexts the deletion job has done,
so a stopped run resumes) and `erased_at`. No data step: no Betrieb is
offboarded before this migration.

Revision ID: c7c0901ad259
Revises: d72281c678c0
Create Date: 2026-11-14 00:32:36.849007

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7c0901ad259"
down_revision: str | Sequence[str] | None = "d72281c678c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "export_job",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", app.core.types.GUID(), nullable=False, comment="The requesting user (platform)."),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("produced_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("object_key", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("download_expires_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by",
            app.core.types.GUID(),
            nullable=True,
            comment="The manager who acknowledged it (platform user).",
        ),
        sa.Column("expired_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint("kind IN ('self_service', 'archive')", name="ck_export_job_kind_enum"),
        sa.CheckConstraint("status IN ('requested', 'ready', 'failed', 'expired')", name="ck_export_job_status_enum"),
        sa.ForeignKeyConstraint(
            ["dealership_id"],
            ["dealership.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_export_job_dealership_id_created_at", "export_job", ["dealership_id", "created_at"], unique=False
    )
    op.add_column("dealership", sa.Column("offboarded_at", app.core.types.UTCDateTime(timezone=True), nullable=True))
    op.add_column("dealership", sa.Column("retention_until", sa.Date(), nullable=True))
    op.add_column("dealership", sa.Column("erasure_progress", sa.JSON(), nullable=True))
    op.add_column("dealership", sa.Column("erased_at", app.core.types.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("dealership", "erased_at")
    op.drop_column("dealership", "erasure_progress")
    op.drop_column("dealership", "retention_until")
    op.drop_column("dealership", "offboarded_at")
    op.drop_index("ix_export_job_dealership_id_created_at", table_name="export_job")
    op.drop_table("export_job")
