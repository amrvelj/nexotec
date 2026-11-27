"""KAN-813: the stay of the car — service_stay, service_order_stay,
service_order_workshop_status

ADR-213, Decision C; build slice 1, sections 3.4 and 3.9. One running stay
per car and Betrieb is a partial unique index. The car and the location are
the three-column pattern (rule 2). The workshop status projection is
filled for every existing order as «planned»: no car was received before
this migration, so no order has a stay.

Revision ID: b52e9f0c7d14
Revises: 8d41c2e7a915
Create Date: 2026-11-27 15:00:00

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa

import app.core.types

# revision identifiers, used by Alembic.
revision: str = 'b52e9f0c7d14'
down_revision: Union[str, Sequence[str], None] = '8d41c2e7a915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('service_stay',
    sa.Column('vehicle_id', app.core.types.GUID(), nullable=False, comment='Owned by the vehicle context (VehicleMdm.id). No DB-level FK.'),
    sa.Column('vehicle_label', sa.String(length=200), nullable=False),
    sa.Column('vehicle_label_refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('location_id', app.core.types.GUID(), nullable=False, comment='Owned by the platform context (Location.id). No DB-level FK.'),
    sa.Column('location_label', sa.String(length=200), nullable=False),
    sa.Column('location_label_refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('parking_place', sa.String(length=100), nullable=True),
    sa.Column('key_tag', sa.String(length=50), nullable=True),
    sa.Column('arrived_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('arrived_by', app.core.types.GUID(), nullable=True),
    sa.Column('arrival_channel', sa.String(length=16), nullable=False),
    sa.Column('arrival_mileage', sa.Integer(), nullable=False),
    sa.Column('previous_mileage', sa.Integer(), nullable=True),
    sa.Column('arrival_remark', sa.String(length=1000), nullable=True),
    sa.Column('departed_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('departed_by', app.core.types.GUID(), nullable=True),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.CheckConstraint("arrival_channel IN ('desk')", name='ck_service_stay_arrival_channel_enum'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_service_stay_tenant_id'), 'service_stay', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_service_stay_vehicle_id'), 'service_stay', ['vehicle_id'], unique=False)
    op.create_index('uq_service_stay_running_vehicle', 'service_stay', ['tenant_id', 'vehicle_id'], unique=True, postgresql_where=sa.text('departed_at IS NULL'), sqlite_where=sa.text('departed_at IS NULL'))
    op.create_table('service_order_stay',
    sa.Column('service_order_id', app.core.types.GUID(), nullable=False),
    sa.Column('stay_id', app.core.types.GUID(), nullable=False),
    sa.Column('joined_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.ForeignKeyConstraint(['service_order_id'], ['service_order.id'], ),
    sa.ForeignKeyConstraint(['stay_id'], ['service_stay.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('service_order_id', 'stay_id', name='uq_service_order_stay_order_stay')
    )
    op.create_index(op.f('ix_service_order_stay_service_order_id'), 'service_order_stay', ['service_order_id'], unique=False)
    op.create_index(op.f('ix_service_order_stay_stay_id'), 'service_order_stay', ['stay_id'], unique=False)
    op.create_index(op.f('ix_service_order_stay_tenant_id'), 'service_order_stay', ['tenant_id'], unique=False)
    op.create_table('service_order_workshop_status',
    sa.Column('service_order_id', app.core.types.GUID(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.CheckConstraint("status IN ('planned', 'car_here', 'in_work', 'work_done', 'checked', 'ready_for_pickup', 'handed_over')", name='ck_service_order_workshop_status_status_enum'),
    sa.ForeignKeyConstraint(['service_order_id'], ['service_order.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('service_order_id', name='uq_service_order_workshop_status_order')
    )
    op.create_index(op.f('ix_service_order_workshop_status_tenant_id'), 'service_order_workshop_status', ['tenant_id'], unique=False)
    if context.is_offline_mode():
        op.execute("-- service_order_workshop_status backfill skipped: it reads rows and needs a live database")
    else:
        # Every existing order is «planned»: no car was received before this migration.
        # The id is a UUIDv4 here; the application writes UUIDv7 from now on.
        op.execute(
            "INSERT INTO service_order_workshop_status (id, tenant_id, service_order_id, status, refreshed_at) "
            "SELECT gen_random_uuid(), tenant_id, id, 'planned', now() FROM service_order"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_service_order_workshop_status_tenant_id'), table_name='service_order_workshop_status')
    op.drop_table('service_order_workshop_status')
    op.drop_index(op.f('ix_service_order_stay_tenant_id'), table_name='service_order_stay')
    op.drop_index(op.f('ix_service_order_stay_stay_id'), table_name='service_order_stay')
    op.drop_index(op.f('ix_service_order_stay_service_order_id'), table_name='service_order_stay')
    op.drop_table('service_order_stay')
    op.drop_index('uq_service_stay_running_vehicle', table_name='service_stay', postgresql_where=sa.text('departed_at IS NULL'), sqlite_where=sa.text('departed_at IS NULL'))
    op.drop_index(op.f('ix_service_stay_vehicle_id'), table_name='service_stay')
    op.drop_index(op.f('ix_service_stay_tenant_id'), table_name='service_stay')
    op.drop_table('service_stay')
