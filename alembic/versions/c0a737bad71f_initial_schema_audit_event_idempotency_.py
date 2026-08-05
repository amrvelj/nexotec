"""initial schema: audit_event, idempotency_record

Revision ID: c0a737bad71f
Revises:
Create Date: 2026-08-05 16:36:55.825922

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c0a737bad71f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSON, nullable=True),
        sa.Column("after", postgresql.JSON, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_event_entity_type", "audit_event", ["entity_type"])
    op.create_index("ix_audit_event_entity_id", "audit_event", ["entity_id"])
    op.create_index("ix_audit_event_tenant_id", "audit_event", ["tenant_id"])
    op.create_index(
        "ix_audit_event_entity_type_entity_id", "audit_event", ["entity_type", "entity_id"]
    )

    op.create_table(
        "idempotency_record",
        sa.Column("idempotency_key", sa.String(length=255), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_path", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_record")
    op.drop_index("ix_audit_event_entity_type_entity_id", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_id", table_name="audit_event")
    op.drop_index("ix_audit_event_entity_id", table_name="audit_event")
    op.drop_index("ix_audit_event_entity_type", table_name="audit_event")
    op.drop_table("audit_event")
