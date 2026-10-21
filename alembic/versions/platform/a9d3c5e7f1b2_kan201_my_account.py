"""user: correspondence language and signature block; user_connected_account; auth_event kind (KAN-201)

My account (Dealer Administration, Users → Personal settings; Authentication
& Identity FR-AU-23/24, D-A-15):

- `user.correspondence_language` — the language of documents the user
  authors where the document has no customer language of its own; NULL
  means the Betrieb's default. Nullable, so nothing is required of an
  existing row.
- `user.signature_block` — the plain-text signature lines; nullable.
- `user_connected_account` — an OAuth grant to act on the user's behalf,
  never an identity: provider, external account id and label, scopes, a
  reference into the secrets manager (never a token), status, linked /
  last-used / revoked stamps. Empty until the Outlook integration links the
  first account.
- `auth_event.kind` gains `connected_account_revoked`: the CHECK is
  replaced with the full list (tests/test_migrated_enum_checks.py).

Downgrade drops the table and the two columns and restores the CHECK; a
`connected_account_revoked` row would violate it, so those rows are
deleted first — for a failed deploy only (before the Outlook integration
links any account: dropping the table would drop secret references).

Revision ID: a9d3c5e7f1b2
Revises: d8e2f4a6c9b1
Create Date: 2026-10-20 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d3c5e7f1b2"
down_revision: str | Sequence[str] | None = "d8e2f4a6c9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
_LANGUAGES = ("de", "fr", "it", "en")
_PROVIDERS = ("microsoft_365",)
_STATUSES = ("active", "error", "revoked")
_REVOKED_REASONS = ("user", "manager", "deactivated")
_EVENT_KINDS_BEFORE = (
    "login_succeeded",
    "login_failed",
    "login_throttled",
    "locked_out",
    "password_set",
    "password_reset_requested",
    "password_reset",
    "invitation_issued",
    "invitation_accepted",
    "mfa_enrolled",
    "mfa_removed",
    "mfa_failed",
    "recovery_code_used",
    "recovery_codes_regenerated",
    "session_revoked",
    "credential_rehashed",
    "breach_check_unavailable",
    "mail_delivery_failed",
    "email_changed",
)
_EVENT_KINDS = (*_EVENT_KINDS_BEFORE, "connected_account_revoked")


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("user", sa.Column("correspondence_language", sa.String(length=8), nullable=True))
    op.create_check_constraint(
        "ck_user_correspondence_language_enum", "user", _check("correspondence_language", _LANGUAGES)
    )
    op.add_column("user", sa.Column("signature_block", sa.String(length=1000), nullable=True))

    op.create_table(
        "user_connected_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("external_account_label", sa.String(length=255), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=16), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(_check("provider", _PROVIDERS), name="ck_user_connected_account_provider_enum"),
        sa.CheckConstraint(_check("status", _STATUSES), name="ck_user_connected_account_status_enum"),
        sa.CheckConstraint(
            _check("revoked_reason", _REVOKED_REASONS), name="ck_user_connected_account_revoked_reason_enum"
        ),
    )
    op.create_index("ix_user_connected_account_user_id", "user_connected_account", ["user_id"])
    # One live grant per user, provider and account; a revoked row stays.
    op.create_index(
        "uq_user_connected_account_live",
        "user_connected_account",
        ["user_id", "provider", "external_account_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'revoked'"),
    )

    op.drop_constraint("ck_auth_event_kind_enum", "auth_event", type_="check")
    op.create_check_constraint("ck_auth_event_kind_enum", "auth_event", _check("kind", _EVENT_KINDS))


def downgrade() -> None:
    op.execute("DELETE FROM auth_event WHERE kind = 'connected_account_revoked'")
    op.drop_constraint("ck_auth_event_kind_enum", "auth_event", type_="check")
    op.create_check_constraint("ck_auth_event_kind_enum", "auth_event", _check("kind", _EVENT_KINDS_BEFORE))

    op.drop_index("uq_user_connected_account_live", table_name="user_connected_account")
    op.drop_index("ix_user_connected_account_user_id", table_name="user_connected_account")
    op.drop_table("user_connected_account")
    op.drop_column("user", "signature_block")
    op.drop_constraint("ck_user_correspondence_language_enum", "user", type_="check")
    op.drop_column("user", "correspondence_language")
