"""KAN-215: a user's own Google Workspace mailbox as a connected account, and
the auth event of a link.

- `user_connected_account.provider` gains `google_workspace` (the Gmail API's
  `gmail.send` grant, linked by the user to send customer mail as themselves).
- `auth_event.kind` gains `connected_account_linked`.
The CHECKs are replaced with the full lists (tests/test_migrated_enum_checks.py).
No column, no table.

Downgrade restores both CHECKs. It refuses while a `google_workspace` grant row
exists — it may hold a reference to a token in the secrets manager that a
deleted row would orphan (the KAN-550 rule); revoke and end those grants first.
`connected_account_linked` rows are deleted first, for a failed deploy only
(the audit log keeps its own copy of each link).

Revision ID: c2a7e5d9f4b1
Revises: b8d2f4a61c39
Create Date: 2026-11-18 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c2a7e5d9f4b1"
down_revision: str | Sequence[str] | None = "b8d2f4a61c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
_PROVIDERS_BEFORE = ("microsoft_365",)
_PROVIDERS = ("microsoft_365", "google_workspace")
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
    "recovery_invitation_reissued",
    "recovery_mfa_reset",
    "recovery_lockout_cleared",
    "recovery_sessions_revoked",
    "recovery_manager_granted",
    "recovery_email_changed",
)
_EVENT_KINDS = (
    *_EVENT_KINDS_BEFORE[: _EVENT_KINDS_BEFORE.index("connected_account_revoked") + 1],
    "connected_account_linked",
    *_EVENT_KINDS_BEFORE[_EVENT_KINDS_BEFORE.index("connected_account_revoked") + 1 :],
)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def _replace(table: str, column: str, values: tuple[str, ...]) -> None:
    name = f"ck_{table}_{column}_enum"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, _check(column, values))


def upgrade() -> None:
    _replace("user_connected_account", "provider", _PROVIDERS)
    _replace("auth_event", "kind", _EVENT_KINDS)


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- the check for google_workspace grants skipped: it reads rows and needs a live database")
    else:
        held = op.get_bind().execute(
            sa.text("SELECT count(*) FROM user_connected_account WHERE provider = 'google_workspace'")
        ).scalar_one()
        if held:
            raise RuntimeError(
                f"{held} Google Workspace connected account(s) exist; they may reference tokens in the secrets "
                "manager. Revoke them and let the provider end run, then downgrade again."
            )
    op.execute("DELETE FROM auth_event WHERE kind = 'connected_account_linked'")
    _replace("auth_event", "kind", _EVENT_KINDS_BEFORE)
    _replace("user_connected_account", "provider", _PROVIDERS_BEFORE)
