"""dealership_membership.granted_by / valid_to and standard_text_delegation (KAN-199, D-A-01, FR-A-13, FR-A-28, D-A-23)

Memberships become grantable through the API (KAN-135): `granted_by` names
the granter, `valid_to` ends the access — a removed sister-Betrieb access
keeps its row with an end date, never deleted (Anto, 2026-10-17). Existing
rows keep `granted_by` NULL and stay active (`valid_to` NULL).

`standard_text_delegation` (FR-A-28): one row per (user, category) write
grant, reopened on a re-grant, ended with `valid_to`; `category` is the
PRD's five standard-text categories, enforced by a CHECK.

Downgrade drops the table and the two columns; an ended membership would
read as active again, which is why the downgrade is for a failed deploy
only.

Revision ID: b7e4d2a9c153
Revises: 9c2d7e4b1a56
Create Date: 2026-10-17 14:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4d2a9c153"
down_revision: str | Sequence[str] | None = "9c2d7e4b1a56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen: app.platform.models.standard_text_delegation.StandardTextCategory at
# this revision, in enum order (the CHECK's wording is enum_check_condition's).
_CATEGORIES = ("document", "email", "sms", "internal_note", "workshop")


def upgrade() -> None:
    op.add_column("dealership_membership", sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("dealership_membership", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "standard_text_delegation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            '"category" IN (' + ", ".join(f"'{c}'" for c in _CATEGORIES) + ")",
            name="ck_standard_text_delegation_category_enum",
        ),
        sa.UniqueConstraint("user_id", "category", name="uq_standard_text_delegation_user_id_category"),
    )
    op.create_index("ix_standard_text_delegation_dealership_id", "standard_text_delegation", ["dealership_id"])
    op.create_index("ix_standard_text_delegation_user_id", "standard_text_delegation", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_standard_text_delegation_user_id", table_name="standard_text_delegation")
    op.drop_index("ix_standard_text_delegation_dealership_id", table_name="standard_text_delegation")
    op.drop_table("standard_text_delegation")
    op.drop_column("dealership_membership", "valid_to")
    op.drop_column("dealership_membership", "granted_by")
