"""valuation's own «carried by a pending / signed contract» rows (KAN-631)

`valuation_contract_carry`: one row per (valuation, contract), written by the
calls Sales makes into `valuation.public` (ADR-047 Pattern B), so valuation
answers «is this valuation carried by a pending or a signed contract» from
its own data instead of asking Sales (ADR-131). New table, no data step:
the rows for contracts written before it are filled by
`scripts/backfill_valuation_contract_carry.py` (dry run, then `--commit`,
once per environment after the deploy). `contract_id` names a Sales
contract (rule 2: no DB-level FK). The unique constraint's leading column
serves the lookups by valuation.

Revision ID: 854d8cd3e4c7
Revises: da2a32664e44
Create Date: 2026-11-12 17:09:11.227717

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.core.types

# revision identifiers, used by Alembic.
revision: str = '854d8cd3e4c7'
down_revision: Union[str, Sequence[str], None] = 'da2a32664e44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('valuation_contract_carry',
    sa.Column('valuation_id', app.core.types.GUID(), nullable=False),
    sa.Column('contract_id', app.core.types.GUID(), nullable=False, comment='Owned by the sales context (SalesContract.id). No DB-level FK.'),
    sa.Column('contract_label', sa.String(length=64), nullable=True),
    sa.Column('contract_label_refreshed_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('signed_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.CheckConstraint("state IN ('pending', 'signed', 'released')", name='ck_valuation_contract_carry_state_enum'),
    sa.ForeignKeyConstraint(['valuation_id'], ['valuation.id'], name='fk_valuation_contract_carry_valuation_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('valuation_id', 'contract_id', name='uq_valuation_contract_carry_valuation_contract')
    )
    op.create_index(op.f('ix_valuation_contract_carry_contract_id'), 'valuation_contract_carry', ['contract_id'], unique=False)
    op.create_index(op.f('ix_valuation_contract_carry_tenant_id'), 'valuation_contract_carry', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_valuation_contract_carry_tenant_id'), table_name='valuation_contract_carry')
    op.drop_index(op.f('ix_valuation_contract_carry_contract_id'), table_name='valuation_contract_carry')
    op.drop_table('valuation_contract_carry')
