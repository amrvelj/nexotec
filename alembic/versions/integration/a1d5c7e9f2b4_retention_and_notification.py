"""retention and notification tables (WP-6 PR-6, ADR-024/ADR-025)

`integration_call_payload` — the ONE place a raw provider payload ever
exists, encrypted at rest, structurally separate from `integration_call_
log` (see that table's own model docstring). `integration_notification`
— one row per notification actually sent (expiry warning / break-glass
access / the daily support digest); no DB-level uniqueness constraint,
since the right dedup shape differs per `kind` — see the model's own
docstring.

Revision ID: a1d5c7e9f2b4
Revises: f3c9a1e6d8b2
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1d5c7e9f2b4'
down_revision: Union[str, Sequence[str], None] = 'f3c9a1e6d8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_call_payload",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "call_log_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integration_call_log.id"), nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_call_payload_call_log_id", "integration_call_payload", ["call_log_id"])
    op.create_index("ix_integration_call_payload_tenant_id", "integration_call_payload", ["tenant_id"])
    op.create_unique_constraint(
        "uq_integration_call_payload_call_log_id", "integration_call_payload", ["call_log_id"]
    )

    op.create_table(
        "integration_notification",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("threshold_days", sa.Integer(), nullable=True),
        sa.Column("sent_date", sa.Date(), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_integration_notification_connection_id", "integration_notification", ["connection_id"])
    op.create_index("ix_integration_notification_tenant_id", "integration_notification", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("integration_notification")
    op.drop_table("integration_call_payload")
