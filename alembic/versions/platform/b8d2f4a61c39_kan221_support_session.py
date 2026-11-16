"""KAN-221 PR-2: support_session — a Nexotec support session's record (staff, user, Betrieb, reason, 60 minutes, how it ended)

Revision ID: b8d2f4a61c39
Revises: a070f46c70ee
Create Date: 2026-11-16 14:41:32.625157

"""
from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d2f4a61c39'
down_revision: str | Sequence[str] | None = 'a070f46c70ee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('support_session',
    sa.Column('staff_user_id', app.core.types.GUID(), nullable=False),
    sa.Column('target_user_id', app.core.types.GUID(), nullable=False),
    sa.Column('dealership_id', app.core.types.GUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('started_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('expires_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('ended_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('ended_by', sa.String(length=16), nullable=True),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.CheckConstraint("ended_by IN ('staff', 'user', 'timeout')", name='ck_support_session_ended_by_enum'),
    sa.ForeignKeyConstraint(['dealership_id'], ['dealership.id'], ),
    sa.ForeignKeyConstraint(['staff_user_id'], ['user.id'], ),
    sa.ForeignKeyConstraint(['target_user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_support_session_dealership_id'), 'support_session', ['dealership_id'], unique=False)
    op.create_index(op.f('ix_support_session_staff_user_id'), 'support_session', ['staff_user_id'], unique=False)
    op.create_index(op.f('ix_support_session_target_user_id'), 'support_session', ['target_user_id'], unique=False)
    op.create_index('uq_support_session_staff_open', 'support_session', ['staff_user_id'], unique=True, postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_support_session_staff_open', table_name='support_session', postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
    op.drop_index(op.f('ix_support_session_target_user_id'), table_name='support_session')
    op.drop_index(op.f('ix_support_session_staff_user_id'), table_name='support_session')
    op.drop_index(op.f('ix_support_session_dealership_id'), table_name='support_session')
    op.drop_table('support_session')
