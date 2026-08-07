"""customer PRD phase A: business/individual fields, phone/email/external-id tables

Revision ID: e9dd878c7836
Revises: 45e33bf8cdbf
Create Date: 2026-08-07 19:01:43.584580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e9dd878c7836'
down_revision: Union[str, Sequence[str], None] = '45e33bf8cdbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'customer_email',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'email_type',
            sa.Enum('PRIVATE', 'BUSINESS', name='emailtype', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('email_address', sa.String(length=254), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['dealer.id']),
        sa.UniqueConstraint('customer_id', 'email_address', name='uq_customer_email_customer_id_address'),
    )
    op.create_index(op.f('ix_customer_email_customer_id'), 'customer_email', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_email_tenant_id'), 'customer_email', ['tenant_id'], unique=False)

    op.create_table(
        'customer_external_id',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('system_name', sa.String(length=100), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['dealer.id']),
        sa.UniqueConstraint('customer_id', 'system_name', name='uq_customer_external_id_customer_system'),
        sa.UniqueConstraint(
            'tenant_id', 'system_name', 'external_id', name='uq_customer_external_id_tenant_system_external'
        ),
    )
    op.create_index(
        op.f('ix_customer_external_id_customer_id'), 'customer_external_id', ['customer_id'], unique=False
    )
    op.create_index(
        op.f('ix_customer_external_id_tenant_id'), 'customer_external_id', ['tenant_id'], unique=False
    )

    op.create_table(
        'customer_phone',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'phone_type',
            sa.Enum('MOBILE', 'PRIVATE', 'OFFICE', name='phonetype', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('phone_e164', sa.String(length=20), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['dealer.id']),
        sa.UniqueConstraint('customer_id', 'phone_e164', name='uq_customer_phone_customer_id_e164'),
    )
    op.create_index(op.f('ix_customer_phone_customer_id'), 'customer_phone', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_phone_tenant_id'), 'customer_phone', ['tenant_id'], unique=False)

    op.add_column('customer', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('customer', sa.Column('nationality', sa.String(length=2), nullable=True))
    op.add_column('customer', sa.Column('company_name', sa.String(length=200), nullable=True))
    op.add_column(
        'customer',
        sa.Column(
            'legal_form',
            sa.Enum(
                'AG', 'GMBH', 'EINZELFIRMA', 'VEREIN', 'GENOSSENSCHAFT', 'WEITERE',
                name='legalform', native_enum=False, length=32,
            ),
            nullable=True,
        ),
    )
    op.add_column('customer', sa.Column('tax_id', sa.Text(), nullable=True))
    op.add_column(
        'customer',
        sa.Column(
            'preferred_channel',
            sa.Enum('MAIL', 'CALL', 'MESSAGE', 'LETTER', name='preferredchannel', native_enum=False, length=16),
            nullable=True,
        ),
    )
    op.alter_column('customer', 'first_name', existing_type=sa.String(length=100), nullable=True)
    op.alter_column('customer', 'last_name', existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('customer', 'last_name', existing_type=sa.String(length=100), nullable=False)
    op.alter_column('customer', 'first_name', existing_type=sa.String(length=100), nullable=False)
    op.drop_column('customer', 'preferred_channel')
    op.drop_column('customer', 'tax_id')
    op.drop_column('customer', 'legal_form')
    op.drop_column('customer', 'company_name')
    op.drop_column('customer', 'nationality')
    op.drop_column('customer', 'birth_date')
    op.drop_index(op.f('ix_customer_phone_tenant_id'), table_name='customer_phone')
    op.drop_index(op.f('ix_customer_phone_customer_id'), table_name='customer_phone')
    op.drop_table('customer_phone')
    op.drop_index(op.f('ix_customer_external_id_tenant_id'), table_name='customer_external_id')
    op.drop_index(op.f('ix_customer_external_id_customer_id'), table_name='customer_external_id')
    op.drop_table('customer_external_id')
    op.drop_index(op.f('ix_customer_email_tenant_id'), table_name='customer_email')
    op.drop_index(op.f('ix_customer_email_customer_id'), table_name='customer_email')
    op.drop_table('customer_email')
