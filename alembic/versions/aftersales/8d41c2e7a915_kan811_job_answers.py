"""KAN-811: the customer's answer per job — service_order_job_answer; «stopped»
on service_order_job

Build slice 1, table 3.2 (rulings 30, 48, 167, 172). Every answer is its own
append-only row, with how it came, who, when, a note and the amount said yes
to (null when the job had no price). A job carries «stopped» with the
advisor's choice «hours billed». Existing jobs are not stopped: the three new
columns are null.

Revision ID: 8d41c2e7a915
Revises: 724e2f43cc9e
Create Date: 2026-11-27 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.core.types

# revision identifiers, used by Alembic.
revision: str = '8d41c2e7a915'
down_revision: Union[str, Sequence[str], None] = '724e2f43cc9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('service_order_job_answer',
    sa.Column('service_order_id', app.core.types.GUID(), nullable=False),
    sa.Column('job_id', app.core.types.GUID(), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=1000), nullable=True),
    sa.Column('amount_net', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('amount_gross', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('hours_billed', sa.Boolean(), nullable=True),
    sa.Column('recorded_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('recorded_by', app.core.types.GUID(), nullable=True),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.CheckConstraint("result IN ('accepted', 'declined')", name='ck_service_order_job_answer_result_enum'),
    sa.CheckConstraint("channel IN ('desk', 'phone', 'message')", name='ck_service_order_job_answer_channel_enum'),
    sa.ForeignKeyConstraint(['job_id'], ['service_order_job.id'], ),
    sa.ForeignKeyConstraint(['service_order_id'], ['service_order.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_service_order_job_answer_job_id'), 'service_order_job_answer', ['job_id'], unique=False)
    op.create_index(op.f('ix_service_order_job_answer_service_order_id'), 'service_order_job_answer', ['service_order_id'], unique=False)
    op.create_index(op.f('ix_service_order_job_answer_tenant_id'), 'service_order_job_answer', ['tenant_id'], unique=False)
    op.add_column('service_order_job', sa.Column('stopped_at', app.core.types.UTCDateTime(timezone=True), nullable=True))
    op.add_column('service_order_job', sa.Column('stopped_by', app.core.types.GUID(), nullable=True))
    op.add_column('service_order_job', sa.Column('hours_billed', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('service_order_job', 'hours_billed')
    op.drop_column('service_order_job', 'stopped_by')
    op.drop_column('service_order_job', 'stopped_at')
    op.drop_index(op.f('ix_service_order_job_answer_tenant_id'), table_name='service_order_job_answer')
    op.drop_index(op.f('ix_service_order_job_answer_service_order_id'), table_name='service_order_job_answer')
    op.drop_index(op.f('ix_service_order_job_answer_job_id'), table_name='service_order_job_answer')
    op.drop_table('service_order_job_answer')
