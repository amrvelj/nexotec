"""KAN-216 — staff notifications (Dealer Administration D-A-40, FR-A-47…50)

Four platform tables:

- `notification_event` — the catalogue (FR-A-47), seeded here with the
  `security` family (FR-AU-12, FR-AU-26): an event not in the catalogue
  cannot be sent. A module adds its own events with a migration of this
  chain in the PR that raises them. The rows are written out literally — a
  migration never reads the application's declaration
  (`app/platform/services/notification_catalogue.py`), which moves on;
  `tests/test_migrated_notification_catalogue.py` keeps both equal.
- `user_notification_preference` — per user per family (FR-A-48).
- `notification` — the inbox, the record (FR-A-50), 90 days.
- `push_subscription` — Web Push per session (FR-A-49), keys encrypted.

No data is read: the seed renders offline as plain INSERTs.

Revision ID: 994244560c8a
Revises: c4f1a9e2b7d3
Create Date: 2026-10-31 15:53:22.559874

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "994244560c8a"
down_revision: str | Sequence[str] | None = "c4f1a9e2b7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (id, code, i18n key stem) — every security event is immediate and not
# switchable (FR-A-48). Fixed ids, so every environment holds the same rows.
_SECURITY_EVENTS = (
    ("019a3a40-0000-7000-8000-000000000001", "security.new_device_login", "newDeviceLogin"),
    ("019a3a40-0000-7000-8000-000000000002", "security.factor_enrolled", "factorEnrolled"),
    ("019a3a40-0000-7000-8000-000000000003", "security.factor_removed", "factorRemoved"),
    ("019a3a40-0000-7000-8000-000000000004", "security.recovery_code_used", "recoveryCodeUsed"),
    ("019a3a40-0000-7000-8000-000000000005", "security.account_locked", "accountLocked"),
    ("019a3a40-0000-7000-8000-000000000006", "security.password_changed", "passwordChanged"),
    ("019a3a40-0000-7000-8000-000000000007", "security.session_revoked_by_other", "sessionRevokedByOther"),
    ("019a3a40-0000-7000-8000-000000000008", "security.impersonation_started", "impersonationStarted"),
    ("019a3a40-0000-7000-8000-000000000009", "security.impersonation_ended", "impersonationEnded"),
    ("019a3a40-0000-7000-8000-00000000000a", "security.connected_account_linked", "connectedAccountLinked"),
    ("019a3a40-0000-7000-8000-00000000000b", "security.connected_account_revoked", "connectedAccountRevoked"),
    ("019a3a40-0000-7000-8000-00000000000c", "security.platform_recovery_action", "platformRecoveryAction"),
)


def upgrade() -> None:
    notification_event = op.create_table(
        "notification_event",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("default_timing", sa.String(length=16), nullable=False),
        sa.Column("switchable", sa.Boolean(), nullable=False),
        sa.Column("title_key", sa.String(length=128), nullable=False),
        sa.Column("body_key", sa.String(length=128), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.CheckConstraint(
            "default_timing IN ('immediate', 'digest', 'off')", name="ck_notification_event_default_timing_enum"
        ),
        sa.CheckConstraint(
            "family IN ('security', 'administration', 'assignment')", name="ck_notification_event_family_enum"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_notification_event_code"),
    )
    op.create_table(
        "user_notification_preference",
        sa.Column("user_id", app.core.types.GUID(), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("timing", sa.String(length=16), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False),
        sa.Column("push", sa.Boolean(), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "family IN ('security', 'administration', 'assignment')",
            name="ck_user_notification_preference_family_enum",
        ),
        sa.CheckConstraint(
            "timing IN ('immediate', 'digest', 'off')", name="ck_user_notification_preference_timing_enum"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "family", name="uq_user_notification_preference_user_family"),
    )
    op.create_index(
        op.f("ix_user_notification_preference_user_id"), "user_notification_preference", ["user_id"], unique=False
    )
    op.create_table(
        "notification",
        sa.Column("user_id", app.core.types.GUID(), nullable=False),
        sa.Column("dealership_id", app.core.types.GUID(), nullable=True),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", app.core.types.GUID(), nullable=True),
        sa.Column("link", sa.String(length=512), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("read_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.ForeignKeyConstraint(["event_code"], ["notification_event.code"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_created_at", "notification", ["created_at"], unique=False)
    op.create_index("ix_notification_user_id_created_at", "notification", ["user_id", "created_at"], unique=False)
    op.create_table(
        "push_subscription",
        sa.Column("user_id", app.core.types.GUID(), nullable=False),
        sa.Column("session_id", app.core.types.GUID(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", app.core.types.EncryptedString(), nullable=False),
        sa.Column("auth", app.core.types.EncryptedString(), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_hash", name="uq_push_subscription_endpoint_hash"),
    )
    op.create_index(op.f("ix_push_subscription_session_id"), "push_subscription", ["session_id"], unique=False)
    op.create_index(op.f("ix_push_subscription_user_id"), "push_subscription", ["user_id"], unique=False)

    op.bulk_insert(
        notification_event,
        [
            {
                "id": event_id,
                "code": code,
                "family": "security",
                "default_timing": "immediate",
                "switchable": False,
                "title_key": f"notifications.events.{stem}.title",
                "body_key": f"notifications.events.{stem}.body",
            }
            for event_id, code, stem in _SECURITY_EVENTS
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_push_subscription_user_id"), table_name="push_subscription")
    op.drop_index(op.f("ix_push_subscription_session_id"), table_name="push_subscription")
    op.drop_table("push_subscription")
    op.drop_index("ix_notification_user_id_created_at", table_name="notification")
    op.drop_index("ix_notification_created_at", table_name="notification")
    op.drop_table("notification")
    op.drop_index(op.f("ix_user_notification_preference_user_id"), table_name="user_notification_preference")
    op.drop_table("user_notification_preference")
    op.drop_table("notification_event")
