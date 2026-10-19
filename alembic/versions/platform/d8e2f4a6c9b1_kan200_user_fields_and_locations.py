"""user: the Users-screen fields; user_location (KAN-200; Dealer Administration Users → Fields, FR-A-02, FR-A-12)

The Users screen edits what the PRD's field table names and the identity
front door did not need: direct dial (`phone` stays the mobile), employee
number, department, line manager, sales advisor code, commission flag, the
validity window (`valid_from` / `valid_to`, calendar days — FR-A-12's
automatic deactivation reads `valid_to`), the lifecycle stamps
(`invited_at`, `last_login_at`, `deactivated_at`, `deactivation_reason`)
and the signature image's object-store key. All nullable or defaulted:
nothing is required of an existing row, no request field becomes required.

`user_location`: primary plus additional sites per user, one primary per
user (partial unique index). Both ends platform-owned — real FKs.

Downgrade drops the table and the thirteen columns; the lifecycle stamps
and the validity windows are lost, so it is for a failed deploy only.

Revision ID: d8e2f4a6c9b1
Revises: c4d8e1f2a6b7
Create Date: 2026-10-19 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e2f4a6c9b1"
down_revision: str | Sequence[str] | None = "c4d8e1f2a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USER_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("direct_dial", sa.String(length=20), nullable=True),
    sa.Column("employee_number", sa.String(length=50), nullable=True),
    sa.Column("department", sa.String(length=100), nullable=True),
    sa.Column("line_manager_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("sales_advisor_code", sa.String(length=50), nullable=True),
    sa.Column("commission_relevant", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("valid_from", sa.Date(), nullable=True),
    sa.Column("valid_to", sa.Date(), nullable=True),
    sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deactivation_reason", sa.String(length=500), nullable=True),
    sa.Column("signature_image_ref", sa.String(length=255), nullable=True),
)


def upgrade() -> None:
    for column in _USER_COLUMNS:
        op.add_column("user", column)

    op.create_table(
        "user_location",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("location.id"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("user_id", "location_id", name="uq_user_location_user_id_location_id"),
    )
    op.create_index("ix_user_location_user_id", "user_location", ["user_id"])
    op.create_index("ix_user_location_location_id", "user_location", ["location_id"])
    op.create_index(
        "uq_user_location_one_primary",
        "user_location",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_location_one_primary", table_name="user_location")
    op.drop_index("ix_user_location_location_id", table_name="user_location")
    op.drop_index("ix_user_location_user_id", table_name="user_location")
    op.drop_table("user_location")
    for column in reversed(_USER_COLUMNS):
        op.drop_column("user", column.name)
