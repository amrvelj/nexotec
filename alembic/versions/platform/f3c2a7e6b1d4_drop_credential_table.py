"""drop credential table (WP-4, closes Gap Analysis G-09)

The interim internal password store. Login now runs entirely through
Zitadel (app.platform.api.auth::oidc_callback, app.platform.services.oidc)
— app.platform.models.credential, app.platform.services.auth, and
app.core.security (bcrypt hash/verify) are all deleted alongside this
migration, not just this table. See
tests/architecture/test_no_password_credential_storage.py for the
"verify with a query, not by inspection" proof this closes G-09 for real.

Downgrade recreates the exact original DDL from e51ba97525e0_credential_
table.py (including its FK and unique constraint) so the CI migration
round trip (upgrade -> downgrade -> upgrade again) stays clean — this is
not a live rollback path anyone should actually use, since no code left in
this branch would ever populate the table again.

Revision ID: f3c2a7e6b1d4
Revises: b8e4c17a9d5f
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3c2a7e6b1d4'
down_revision: Union[str, Sequence[str], None] = 'b8e4c17a9d5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("credential")


def downgrade() -> None:
    op.create_table(
        "credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", name="fk_credential_user_id_user"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint("uq_credential_user_id", "credential", ["user_id"])
