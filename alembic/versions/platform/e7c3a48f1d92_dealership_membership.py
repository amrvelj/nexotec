"""dealership_membership (WP-3 PR-3)

Which OTHER dealerships (beyond a user's home User.tenant_id) a user may
switch their active dealership to. Both User and Dealership are
platform-owned, so real DB foreign keys are used here — unlike the
cross-context GUID+comment convention.

Revision ID: e7c3a48f1d92
Revises: b3f7c92a5e1d
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e7c3a48f1d92'
down_revision: Union[str, Sequence[str], None] = 'b3f7c92a5e1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dealership_membership",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False
        ),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dealership_membership_user_id", "dealership_membership", ["user_id"])
    op.create_index("ix_dealership_membership_dealership_id", "dealership_membership", ["dealership_id"])
    op.create_unique_constraint(
        "uq_dealership_membership_user_id_dealership_id",
        "dealership_membership",
        ["user_id", "dealership_id"],
    )


def downgrade() -> None:
    op.drop_table("dealership_membership")
