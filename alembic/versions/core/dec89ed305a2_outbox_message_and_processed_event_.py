"""outbox_message and processed_event tables

Transactional outbox (PR-4, ADR-006). One shared table in core per the
PR-3 ruling — not one per context; same pattern as audit_event,
idempotency_record and reconciliation_run/orphan.

Revision ID: dec89ed305a2
Revises: 1471175583ae
Create Date: 2026-08-11 08:16:47.431741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'dec89ed305a2'
down_revision: Union[str, Sequence[str], None] = '1471175583ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outbox_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the platform context (Dealer). No DB-level FK.",
        ),
        sa.Column("producer", sa.String(length=32), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PUBLISHED", "DEAD", name="outboxstatus", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_outbox_message_event_type"), "outbox_message", ["event_type"], unique=False)
    op.create_index(op.f("ix_outbox_message_aggregate_id"), "outbox_message", ["aggregate_id"], unique=False)
    op.create_index(op.f("ix_outbox_message_correlation_id"), "outbox_message", ["correlation_id"], unique=False)
    # The poller's only hot query: WHERE status='pending' AND
    # next_attempt_at <= now() ORDER BY id. SQLAlchemy's Enum(native_enum=
    # False) persists the member NAME ('PENDING'), not the value
    # ('pending') — confirmed empirically; must match here too.
    op.create_index(
        "ix_outbox_message_pending_next_attempt_at",
        "outbox_message",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "processed_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("consumer_name", sa.String(length=64), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("processed_event")
    op.drop_index("ix_outbox_message_pending_next_attempt_at", table_name="outbox_message")
    op.drop_index(op.f("ix_outbox_message_correlation_id"), table_name="outbox_message")
    op.drop_index(op.f("ix_outbox_message_aggregate_id"), table_name="outbox_message")
    op.drop_index(op.f("ix_outbox_message_event_type"), table_name="outbox_message")
    op.drop_table("outbox_message")
