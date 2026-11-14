"""The `security` notification family, wired at its sources (KAN-216;
Dealer Administration D-A-40, Auth FR-AU-12, FR-AU-26).

Every account and sign-in fact is already an auth event (FR-AU-20), and
`auth_events.record` is the one place each is written — so that is where the
family hangs: `on_auth_event` runs inside `record`, in the same transaction,
and raises the notification the event owes the affected user. A path that
records the event notifies; a path that rolls back owes nothing.

| Auth event | Notification | When |
|---|---|---|
| `login_succeeded` | `security.new_device_login` | the login's device (its user agent) matches none of the user's earlier sessions, and there was an earlier one (`details.newDevice`, set by the login) |
| `mfa_enrolled` | `security.factor_enrolled` | always |
| `mfa_removed` | `security.factor_removed` | always (FR-AU-12: a manager or staff removal is visible to the user) |
| `recovery_code_used` | `security.recovery_code_used` | always |
| `locked_out` | `security.account_locked` | always |
| `password_reset` | `security.password_changed` | always |
| `password_set` | `security.password_changed` | only when it replaced a password (`details.replaced`): a first password is the invitation's own step |
| `session_revoked` | `security.session_revoked_by_other` | the actor is someone other than the user (a manager's status or role change, platform staff) |
| `connected_account_revoked` | `security.connected_account_revoked` | always |

Platform staff's recovery actions arrive through `security_notifications`
(the KAN-202 seam) as `security.platform_recovery_action`, beside the
mechanism events the action records (`mfa_removed`, `session_revoked`, …) —
each fact is told once. `security.connected_account_linked` and the
impersonation pair are in the catalogue; nothing raises them until linking
(the Outlook integration) and impersonation ([P3-1]) ship.

Every call names its code literally — the architecture test reads them.
"""

import uuid

from sqlalchemy.orm import Session

from app.platform.identity.models.auth_event import AuthEvent, AuthEventKind
from app.platform.identity.services import notifications
from app.platform.identity.services.notification_catalogue import NotificationEventCode

SECURITY_LINK = "/account/security"
CONNECTED_ACCOUNTS_LINK = "/account/connected-accounts"

# The recovery action, as the body's parameter (a closed code; the SPA
# translates it under notifications.recoveryActions.*).
RECOVERY_ACTIONS = {
    AuthEventKind.RECOVERY_INVITATION_REISSUED.value: "invitationReissued",
    AuthEventKind.RECOVERY_MFA_RESET.value: "mfaReset",
    AuthEventKind.RECOVERY_LOCKOUT_CLEARED.value: "lockoutCleared",
    AuthEventKind.RECOVERY_SESSIONS_REVOKED.value: "sessionsRevoked",
    AuthEventKind.RECOVERY_MANAGER_GRANTED.value: "managerGranted",
    AuthEventKind.RECOVERY_EMAIL_CHANGED.value: "emailChanged",
}


def _account(user_id: uuid.UUID) -> notifications.NotificationSubject:
    return notifications.NotificationSubject(type="user", id=user_id, link=SECURITY_LINK)


def on_auth_event(db: Session, event: AuthEvent) -> None:
    user_id = event.user_id
    if user_id is None:
        return
    details = event.details or {}
    kind = event.kind
    dealership_id = event.dealership_id
    if kind == AuthEventKind.LOGIN_SUCCEEDED:
        if details.get("newDevice") is True:
            notifications.notify(
                db,
                NotificationEventCode.NEW_DEVICE_LOGIN,
                subject=_account(user_id),
                user_id=user_id,
                dealership_id=dealership_id,
            )
    elif kind == AuthEventKind.MFA_ENROLLED:
        notifications.notify(
            db,
            NotificationEventCode.FACTOR_ENROLLED,
            subject=_account(user_id),
            user_id=user_id,
            dealership_id=dealership_id,
        )
    elif kind == AuthEventKind.MFA_REMOVED:
        notifications.notify(
            db,
            NotificationEventCode.FACTOR_REMOVED,
            subject=_account(user_id),
            user_id=user_id,
            dealership_id=dealership_id,
        )
    elif kind == AuthEventKind.RECOVERY_CODE_USED:
        remaining = details.get("remaining")
        notifications.notify(
            db,
            NotificationEventCode.RECOVERY_CODE_USED,
            subject=_account(user_id),
            params={"remaining": remaining} if isinstance(remaining, int) else {},
            user_id=user_id,
            dealership_id=dealership_id,
        )
    elif kind == AuthEventKind.LOCKED_OUT:
        notifications.notify(
            db,
            NotificationEventCode.ACCOUNT_LOCKED,
            subject=_account(user_id),
            user_id=user_id,
            dealership_id=dealership_id,
        )
    elif kind == AuthEventKind.PASSWORD_RESET or (kind == AuthEventKind.PASSWORD_SET and details.get("replaced")):
        notifications.notify(
            db,
            NotificationEventCode.PASSWORD_CHANGED,
            subject=_account(user_id),
            user_id=user_id,
            dealership_id=dealership_id,
        )
    elif kind == AuthEventKind.SESSION_REVOKED:
        if event.actor_id is not None and event.actor_id != user_id:
            notifications.notify(
                db,
                NotificationEventCode.SESSION_REVOKED_BY_OTHER,
                subject=_account(user_id),
                user_id=user_id,
                dealership_id=dealership_id,
            )
    elif kind == AuthEventKind.CONNECTED_ACCOUNT_REVOKED:
        account_id = details.get("connectedAccountId")
        notifications.notify(
            db,
            NotificationEventCode.CONNECTED_ACCOUNT_REVOKED,
            subject=notifications.NotificationSubject(
                type="connected_account",
                id=uuid.UUID(account_id) if isinstance(account_id, str) else None,
                link=CONNECTED_ACCOUNTS_LINK,
            ),
            user_id=user_id,
            dealership_id=dealership_id,
        )


def notify_recovery_action(db: Session, *, user_id: uuid.UUID, dealership_id: uuid.UUID, kind: str) -> None:
    """The KAN-202 seam's sink: platform staff acted on the user's account."""

    notifications.notify(
        db,
        NotificationEventCode.PLATFORM_RECOVERY_ACTION,
        user_id=user_id,
        dealership_id=dealership_id,
        subject=_account(user_id),
        params={"action": RECOVERY_ACTIONS[kind]},
    )
