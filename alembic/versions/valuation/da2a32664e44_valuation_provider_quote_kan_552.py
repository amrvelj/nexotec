"""the provider quote a valuation's auto-i-dat figure comes from (KAN-552)

`valuation_provider_quote`: the identAPI's answer for one configured car,
fetched on the advisor's click before the valuation is created (Anto,
2026-11-09, option A — a valuation is never edited after creation, ADR-066);
`valuation.provider_quote_id` names it. New table and a nullable column: no
data step, nothing to backfill.

Revision ID: da2a32664e44
Revises: 58a4f4fa0ba0
Create Date: 2026-11-09 07:00:43.019266

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.core.types

# revision identifiers, used by Alembic.
revision: str = 'da2a32664e44'
down_revision: Union[str, Sequence[str], None] = '58a4f4fa0ba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('valuation_provider_quote',
    sa.Column('configuration_id', app.core.types.GUID(), nullable=False, comment='Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK.'),
    sa.Column('configuration_label', sa.String(length=200), nullable=True),
    sa.Column('configuration_label_refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('fz_key', sa.String(length=64), nullable=False),
    sa.Column('model_year', sa.Integer(), nullable=False),
    sa.Column('first_registration', sa.Date(), nullable=False),
    sa.Column('mileage', sa.Integer(), nullable=False),
    sa.Column('option_keys', sa.JSON(), nullable=False),
    sa.Column('exchange_price', sa.DECIMAL(precision=12, scale=2), nullable=False),
    sa.Column('purchase_price', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('sale_price', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('vehicle_exchange_price', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('options_exchange_price', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('kilometre_correction_exchange', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('kilometre_expected', sa.Integer(), nullable=True),
    sa.Column('exchange_range_from', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('exchange_range_to', sa.DECIMAL(precision=12, scale=2), nullable=True),
    sa.Column('provider_manual', sa.Boolean(), nullable=False),
    sa.Column('screen_messages', sa.JSON(), nullable=False),
    sa.Column('print_messages', sa.JSON(), nullable=False),
    sa.Column('residual_table_state', sa.String(length=16), nullable=False),
    sa.Column('residual_table', sa.JSON(), nullable=False),
    sa.Column('provider_request_ref', app.core.types.GUID(), nullable=False),
    sa.Column('fetched_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.CheckConstraint("residual_table_state IN ('present', 'not_in_contract', 'unavailable', 'declined')", name='ck_valuation_provider_quote_residual_table_state_enum'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_valuation_provider_quote_configuration_id'), 'valuation_provider_quote', ['configuration_id'], unique=False)
    op.create_index(op.f('ix_valuation_provider_quote_provider_request_ref'), 'valuation_provider_quote', ['provider_request_ref'], unique=False)
    op.create_index(op.f('ix_valuation_provider_quote_tenant_id'), 'valuation_provider_quote', ['tenant_id'], unique=False)
    op.add_column('valuation', sa.Column('provider_quote_id', app.core.types.GUID(), nullable=True))
    op.create_index(op.f('ix_valuation_provider_quote_id'), 'valuation', ['provider_quote_id'], unique=False)
    op.create_foreign_key(
        'fk_valuation_provider_quote_id', 'valuation', 'valuation_provider_quote', ['provider_quote_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_valuation_provider_quote_id', 'valuation', type_='foreignkey')
    op.drop_index(op.f('ix_valuation_provider_quote_id'), table_name='valuation')
    op.drop_column('valuation', 'provider_quote_id')
    op.drop_index(op.f('ix_valuation_provider_quote_tenant_id'), table_name='valuation_provider_quote')
    op.drop_index(op.f('ix_valuation_provider_quote_provider_request_ref'), table_name='valuation_provider_quote')
    op.drop_index(op.f('ix_valuation_provider_quote_configuration_id'), table_name='valuation_provider_quote')
    op.drop_table('valuation_provider_quote')
