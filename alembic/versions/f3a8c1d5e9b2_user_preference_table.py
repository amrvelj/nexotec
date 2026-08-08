"""user_preference table

Platform foundation for the data-grid pattern (UI/UX Core Principles §
User-Level Preference Persistence, blocker U-01) — per-user, per-scope
layout state (grid columns, sort, density, saved views). No synthetic id:
primary key is (user_id, scope) per the doc's own schema.

Revision ID: f3a8c1d5e9b2
Revises: b7c1e4a92f10
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f3a8c1d5e9b2'
down_revision: Union[str, Sequence[str], None] = 'b7c1e4a92f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_preference",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", name="fk_user_preference_user_id_user"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "scope", name="pk_user_preference"),
    )


def downgrade() -> None:
    op.drop_table("user_preference")
