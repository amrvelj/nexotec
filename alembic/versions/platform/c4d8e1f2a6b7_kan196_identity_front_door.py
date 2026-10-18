"""The identity front door (KAN-196, D-A-07, D-A-12; Authentication & Identity)

Nexotec owns authentication again. The `credential` table WP-4 dropped
(f3c2a7e6b1d4) returns — argon2id this time, parameters per row, the last
five hashes, lockout on the credential — together with the tables the
Auth & Identity page specifies: `auth_token` (invitation 14 d, reset 1 h,
MFA step 5 min; hashed, single-use), `session` (one row per login, the local
revocation check that replaced Zitadel's live userinfo call), `auth_event`
(the append-only stream), `dealership_auth_config` (auth_method = local only,
MFA enforced by default), `mfa_totp`, `mfa_recovery_code`, `mfa_passkey`.

A forward migration in the platform chain, never an edit of f3c2a7e6b1d4.
No data step: every existing user has no password until invited (the demo
seed sets one from DMS_SEED_DEMO_PASSWORD). Downgrade drops the eight
tables — every session, credential and factor with them; it is for a failed
deploy only.

Revision ID: c4d8e1f2a6b7
Revises: b7e4d2a9c153
Create Date: 2026-10-17 17:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c4d8e1f2a6b7"
down_revision: str | Sequence[str] | None = "b7e4d2a9c153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (the CHECK's wording is
# enum_check_condition's, tests/test_migrated_enum_checks.py).
_TOKEN_KINDS = ("invitation", "password_reset", "mfa_challenge", "passkey_registration")
_AUTH_METHODS = ("local",)
_EVENT_KINDS = (
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


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def _uuid():
    return postgresql.UUID(as_uuid=True)




def upgrade() -> None:
    _lower_case_user_emails()
    op.create_table(
        "credential",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("password_history", sa.JSON(), nullable=False),
        sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breach_check_pending", sa.Boolean(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", _uuid(), nullable=True),
        sa.Column("updated_by", _uuid(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_credential_user_id"),
    )

    op.create_table(
        "auth_token",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_by", _uuid(), nullable=True),
        sa.CheckConstraint(_check("kind", _TOKEN_KINDS), name="ck_auth_token_kind_enum"),
        sa.UniqueConstraint("token_hash", name="uq_auth_token_token_hash"),
    )
    op.create_index("ix_auth_token_user_id", "auth_token", ["user_id"])

    op.create_table(
        "session",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("dealership_id", _uuid(), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("device_summary", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("amr", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_session_user_id", "session", ["user_id"])
    op.create_index("ix_session_dealership_id", "session", ["dealership_id"])

    op.create_table(
        "auth_event",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("user_id", _uuid(), nullable=True),
        sa.Column("dealership_id", _uuid(), nullable=True),
        sa.Column("actor_id", _uuid(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.CheckConstraint(_check("kind", _EVENT_KINDS), name="ck_auth_event_kind_enum"),
    )
    op.create_index("ix_auth_event_user_id", "auth_event", ["user_id"])
    op.create_index("ix_auth_event_dealership_id", "auth_event", ["dealership_id"])
    op.create_index("ix_auth_event_occurred_at", "auth_event", ["occurred_at"])
    op.create_index("ix_auth_event_ip_address_occurred_at", "auth_event", ["ip_address", "occurred_at"])

    op.create_table(
        "dealership_auth_config",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("dealership_id", _uuid(), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("auth_method", sa.String(length=16), nullable=False),
        sa.Column("mfa_enforced", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", _uuid(), nullable=True),
        sa.Column("updated_by", _uuid(), nullable=True),
        sa.CheckConstraint(_check("auth_method", _AUTH_METHODS), name="ck_dealership_auth_config_auth_method_enum"),
        sa.UniqueConstraint("dealership_id", name="uq_dealership_auth_config_dealership_id"),
    )

    op.create_table(
        "mfa_totp",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.Integer(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_mfa_totp_user_id"),
    )

    op.create_table(
        "mfa_recovery_code",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("code_hash", name="uq_mfa_recovery_code_code_hash"),
    )
    op.create_index("ix_mfa_recovery_code_user_id", "mfa_recovery_code", ["user_id"])

    op.create_table(
        "mfa_passkey",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("user_id", _uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("credential_id", sa.String(length=1364), nullable=False),  # 1023 bytes base64url
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),  # unsigned 32-bit counter
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("credential_id", name="uq_mfa_passkey_credential_id"),
    )
    op.create_index("ix_mfa_passkey_user_id", "mfa_passkey", ["user_id"])


def _lower_case_user_emails() -> None:
    """The address becomes the sign-in and recovery channel, resolved
    case-insensitively: every stored address is lower-cased and a CHECK keeps
    it so. Two rows that differ only in case are one mailbox twice — that
    cannot be decided here: the migration prints every collision as a
    `KAN-196 REPORT:` line and aborts before any DDL, for a hand fix.
    Skipped under `--sql` (the offline render has no rows to read)."""

    if context.is_offline_mode():
        op.create_check_constraint("ck_user_email_lower", "user", "email = lower(email)")
        return
    conn = op.get_bind()
    collisions = conn.execute(
        sa.text(
            'SELECT lower(email) AS mailbox, string_agg(email, \', \' ORDER BY email) AS spellings, count(*) AS n '
            'FROM "user" GROUP BY lower(email) HAVING count(*) > 1 ORDER BY lower(email)'
        )
    ).all()
    for mailbox, spellings, n in collisions:
        print(f"KAN-196 REPORT: user e-mail {mailbox!r} is stored {n} times as {spellings} — one mailbox, two users")
    if collisions:
        raise RuntimeError(
            f"KAN-196: {len(collisions)} e-mail address(es) are stored in more than one spelling; "
            "merge or rename those users by hand, then run the upgrade again."
        )
    lowered = conn.execute(sa.text('UPDATE "user" SET email = lower(email) WHERE email <> lower(email)')).rowcount
    print(f"KAN-196: {lowered} user e-mail address(es) lower-cased")
    op.create_check_constraint("ck_user_email_lower", "user", "email = lower(email)")


def downgrade() -> None:
    # IF EXISTS: a database that applied an earlier cut of this (unreleased)
    # revision has no such constraint; the downgrade must still run through.
    op.execute('ALTER TABLE "user" DROP CONSTRAINT IF EXISTS ck_user_email_lower')
    op.drop_index("ix_mfa_passkey_user_id", table_name="mfa_passkey")
    op.drop_table("mfa_passkey")
    op.drop_index("ix_mfa_recovery_code_user_id", table_name="mfa_recovery_code")
    op.drop_table("mfa_recovery_code")
    op.drop_table("mfa_totp")
    op.drop_table("dealership_auth_config")
    for name in (
        "ix_auth_event_ip_address_occurred_at",
        "ix_auth_event_occurred_at",
        "ix_auth_event_dealership_id",
        "ix_auth_event_user_id",
    ):
        op.drop_index(name, table_name="auth_event")
    op.drop_table("auth_event")
    op.drop_index("ix_session_dealership_id", table_name="session")
    op.drop_index("ix_session_user_id", table_name="session")
    op.drop_table("session")
    op.drop_index("ix_auth_token_user_id", table_name="auth_token")
    op.drop_table("auth_token")
    op.drop_table("credential")
