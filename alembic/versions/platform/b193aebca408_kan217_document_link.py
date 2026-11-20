"""KAN-217 PR-4: document_link — secure links to customer documents (ADR-192)

SMS and WhatsApp carry a link to the document instead of the PDF. One row per
link: the issuing Betrieb, the message type, the owning module's document id
(a plain id, rule 2), the SHA-256 of the token (never the token), the expiry
(14 days), the open facts and the rate limit's window. A new table: the
downgrade drops it with its rows (links already sent stop working).

Revision ID: b193aebca408
Revises: a4d9e6c3b7f2
Create Date: 2026-11-20 14:08:26.821427

"""
from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b193aebca408'
down_revision: str | Sequence[str] | None = 'a4d9e6c3b7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('document_link',
    sa.Column('message_type', sa.String(length=48), nullable=False),
    sa.Column('document_id', app.core.types.GUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('first_opened_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_opened_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('open_count', sa.Integer(), nullable=False),
    sa.Column('window_started_at', app.core.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('window_opens', sa.Integer(), nullable=False),
    sa.Column('id', app.core.types.GUID(), nullable=False),
    sa.Column('tenant_id', app.core.types.GUID(), nullable=False),
    sa.Column('created_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', app.core.types.GUID(), nullable=True),
    sa.Column('updated_by', app.core.types.GUID(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash', name='uq_document_link_token_hash')
    )
    op.create_index(op.f('ix_document_link_document_id'), 'document_link', ['document_id'], unique=False)
    op.create_index(op.f('ix_document_link_tenant_id'), 'document_link', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_link_tenant_id'), table_name='document_link')
    op.drop_index(op.f('ix_document_link_document_id'), table_name='document_link')
    op.drop_table('document_link')
