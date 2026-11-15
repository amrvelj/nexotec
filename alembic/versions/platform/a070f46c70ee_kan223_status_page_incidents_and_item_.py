"""KAN-223: the status page — Nexotec's incidents, the notification ledger and
its catalogue row.

- `platform_status_incident` — Nexotec's own platform status, written by staff
  (title and text in four languages, severity, from, to).
- `status_item_state` — the daily job's ledger per Betrieb × status item, so a
  degradation notifies the managers once until it clears.
- The catalogue row `administration.status_item_degraded`, the
  administration family's third event (`notification_catalogue.py`; kept
  equal by `tests/test_migrated_notification_catalogue.py`).

Revision ID: a070f46c70ee
Revises: a7160e5b0c21
Create Date: 2026-11-15 14:15:13.499832

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a070f46c70ee"
down_revision: str | Sequence[str] | None = "a7160e5b0c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEGRADED_EVENT = (
    "019a8780-0000-7000-8000-000000000001",
    "administration.status_item_degraded",
    "statusItemDegraded",
)


def upgrade() -> None:
    op.create_table(
        "platform_status_incident",
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.JSON(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("starts_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("ends_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_by", app.core.types.GUID(), nullable=False, comment="The staff user (platform)."),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint("severity IN ('warning', 'blocked')", name="ck_platform_status_incident_severity_enum"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_status_incident_starts_at", "platform_status_incident", ["starts_at"], unique=False)
    op.create_table(
        "status_item_state",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("item_key", sa.String(length=96), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("changed_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("notified_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.CheckConstraint("state IN ('ok', 'warning', 'blocked')", name="ck_status_item_state_state_enum"),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dealership_id", "item_key", name="uq_status_item_state_dealership_id_item_key"),
    )

    notification_event = sa.table(
        "notification_event",
        sa.column("id", app.core.types.GUID()),
        sa.column("code", sa.String()),
        sa.column("family", sa.String()),
        sa.column("default_timing", sa.String()),
        sa.column("switchable", sa.Boolean()),
        sa.column("title_key", sa.String()),
        sa.column("body_key", sa.String()),
    )
    event_id, code, stem = _DEGRADED_EVENT
    op.bulk_insert(
        notification_event,
        [
            {
                "id": event_id,
                "code": code,
                "family": "administration",
                "default_timing": "immediate",
                "switchable": True,
                "title_key": f"notifications.events.{stem}.title",
                "body_key": f"notifications.events.{stem}.body",
            }
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification WHERE event_code = :code").bindparams(code=_DEGRADED_EVENT[1]))
    op.execute(sa.text("DELETE FROM notification_event WHERE code = :code").bindparams(code=_DEGRADED_EVENT[1]))
    op.drop_table("status_item_state")
    op.drop_index("ix_platform_status_incident_starts_at", table_name="platform_status_incident")
    op.drop_table("platform_status_incident")
