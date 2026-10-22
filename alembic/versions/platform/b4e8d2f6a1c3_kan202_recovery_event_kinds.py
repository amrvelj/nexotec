"""auth_event kind: platform staff's recovery actions (KAN-202)

Authentication & Identity FR-AU-25/26, D-A-16: each recovery action is its
own auth event kind, so the Betrieb's own stream (FR-AU-21) names it —
`recovery_invitation_reissued`, `recovery_mfa_reset`,
`recovery_lockout_cleared`, `recovery_sessions_revoked`,
`recovery_manager_granted` — and `recovery_email_changed` (platform staff
moving an active user's sign-in address; Anto, 2026-10-22). The CHECK is replaced with the full list
(tests/test_migrated_enum_checks.py). No column, no table.

Downgrade restores the CHECK; a recovery row would violate it, so those rows
are deleted first — for a failed deploy only (they are the record of what
platform staff did; the audit log keeps its own copy).

Revision ID: b4e8d2f6a1c3
Revises: a9d3c5e7f1b2
Create Date: 2026-10-22 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e8d2f6a1c3"
down_revision: str | Sequence[str] | None = "a9d3c5e7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
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
    "connected_account_revoked",
)
_RECOVERY_KINDS = (
    "recovery_invitation_reissued",
    "recovery_mfa_reset",
    "recovery_lockout_cleared",
    "recovery_sessions_revoked",
    "recovery_manager_granted",
    "recovery_email_changed",
)
_EVENT_KINDS = (*_EVENT_KINDS_BEFORE, *_RECOVERY_KINDS)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_auth_event_kind_enum", "auth_event", type_="check")
    op.create_check_constraint("ck_auth_event_kind_enum", "auth_event", _check("kind", _EVENT_KINDS))


def downgrade() -> None:
    op.execute("DELETE FROM auth_event WHERE kind IN (" + ", ".join(f"'{k}'" for k in _RECOVERY_KINDS) + ")")
    op.drop_constraint("ck_auth_event_kind_enum", "auth_event", type_="check")
    op.create_check_constraint("ck_auth_event_kind_enum", "auth_event", _check("kind", _EVENT_KINDS_BEFORE))
