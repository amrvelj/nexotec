"""Platform staff's account recovery (Authentication & Identity FR-AU-25…27,
D-A-16; KAN-202).

Five actions, each a platform_admin's alone (D-A-16: no second approver),
from a session with a second factor and never through an impersonation
(`require_staff_recovery_session`): re-issue an invitation, reset MFA,
clear a lockout, revoke every session, grant the manager flag. Each one, in
one transaction with the change it makes:

- requires a stated reason (FR-AU-25), recorded in the auth event's
  `details` — which the Betrieb's manager reads in their auth stream
  (FR-AU-21; Anto, 2026-10-22) — and in the audit row;
- records its own auth event kind (`recovery_*`) with the staff member as
  the actor (FR-AU-26), beside the mechanism's own events;
- writes an audit row (`recovery_<action>`) naming the staff member;
- owes the user a `security` notice (`security_notifications`, filled by
  KAN-216); a re-issued invitation also goes by e-mail, as the flow it is.

Nobody, platform staff included, ever sets or reads a password (FR-AU-10):
nothing here names the stored hash, its history or a function that writes
or checks one (`tests/architecture/test_recovery_never_touches_a_password.py`).

Lock order (KAN-380): the user row first, then the credential, the tokens,
the factors and the sessions.
"""

import enum
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.credential import Credential
from app.platform.models.dealership import Dealership
from app.platform.models.user import User, UserStatus
from app.platform.services import auth_events, identity, mfa, security_notifications
from app.platform.services import membership as membership_service
from app.platform.services import user as user_service
from app.platform.services.mailer import PlatformMailer

# The audit trail and the auth stream show at most this much of a reason.
REASON_MAX_LENGTH = 500
SEARCH_LIMIT = 200


class RecoveryAction(str, enum.Enum):
    """The path segment of `POST /v1/platform/users/{id}/recovery/{action}`.
    Grant-manager is the Betrieb's (`…/dealerships/{id}/recovery/grant-manager`)."""

    REISSUE_INVITATION = "reissue-invitation"
    RESET_MFA = "reset-mfa"
    CLEAR_LOCKOUT = "clear-lockout"
    REVOKE_SESSIONS = "revoke-sessions"


_EVENT_KIND = {
    RecoveryAction.REISSUE_INVITATION: AuthEventKind.RECOVERY_INVITATION_REISSUED,
    RecoveryAction.RESET_MFA: AuthEventKind.RECOVERY_MFA_RESET,
    RecoveryAction.CLEAR_LOCKOUT: AuthEventKind.RECOVERY_LOCKOUT_CLEARED,
    RecoveryAction.REVOKE_SESSIONS: AuthEventKind.RECOVERY_SESSIONS_REVOKED,
}


# --- finding the account ---------------------------------------------------------------


def get_user_for_recovery_or_404(db: Session, user_id: uuid.UUID) -> User:
    """Any Betrieb's user, locked FOR NO KEY UPDATE and re-read (KAN-380).
    Only reachable behind `require_staff_recovery_session`: platform staff
    are the one principal that is not confined to its own tenant."""

    user = db.scalar(
        select(User).where(User.id == user_id).with_for_update(key_share=True).execution_options(populate_existing=True)
    )
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    return user


def search_users(db: Session, *, email: str | None, dealership_id: uuid.UUID | None) -> list[User]:
    """By exact address (case-insensitive), by Betrieb, or both — never a
    partial match across every Betrieb: staff look up the account they were
    asked about, not browse."""

    stmt = select(User)
    if email is not None:
        stmt = stmt.where(func.lower(User.email) == email.strip().lower())
    if dealership_id is not None:
        stmt = stmt.where(User.tenant_id == dealership_id)
    return list(db.scalars(stmt.order_by(User.last_name, User.first_name, User.id).limit(SEARCH_LIMIT)).all())


def account_state(db: Session, user: User) -> dict:
    """What staff need to choose an action — whether a password exists and
    the lock, the factors, the open sessions. Never a hash, a secret or a
    token."""

    now = utcnow()
    credential = db.scalar(select(Credential).where(Credential.user_id == user.id))
    locked = credential is not None and credential.locked_until is not None and credential.locked_until > now
    open_sessions = db.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.idle_expires_at > now,
            AuthSession.absolute_expires_at > now,
        )
    )
    dealership = db.get(Dealership, user.tenant_id)
    return {
        "dealership_name": (dealership.trade_name or dealership.legal_name) if dealership is not None else "",
        "has_password": credential is not None,
        "locked_until": credential.locked_until if locked and credential is not None else None,
        "failed_login_attempts": credential.failed_login_attempts if credential is not None else 0,
        "mfa_enrolled": mfa.has_factor(db, user.id),
        "open_sessions": int(open_sessions or 0),
    }


# --- the actions -----------------------------------------------------------------------


def _clean_reason(reason: str) -> str:
    cleaned = " ".join(reason.split())
    if not cleaned:
        raise BadRequestError("A recovery action needs a stated reason.", details={"rule": "reason_required"})
    return cleaned[:REASON_MAX_LENGTH]


def _assert_recoverable(user: User, *, actor_id: uuid.UUID) -> None:
    if user.id == actor_id:
        # Staff recover someone else's account; their own factors and
        # sessions are theirs to manage in My account (and a staff member
        # who could reset their own factor here would need no second one).
        raise ForbiddenError("Account recovery is for someone else's account.")
    if user.status == UserStatus.DEACTIVATED:
        raise ConflictError(
            "This user is deactivated — the Betrieb reactivates them first.",
            details={"currentStatus": user.status.value},
        )


def _record(
    db: Session,
    *,
    user: User,
    kind: AuthEventKind,
    action: str,
    reason: str,
    actor_id: uuid.UUID,
    ip_address: str,
    details: dict | None = None,
) -> AuthEvent:
    event = auth_events.record(
        db,
        kind,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
        details={"reason": reason, **(details or {})},
    )
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action=f"recovery_{action.replace('-', '_')}",
        actor_id=actor_id,
        after=details or None,
        reason=reason,
    )
    security_notifications.notify(
        db,
        security_notifications.SecurityNotice(
            user_id=user.id,
            dealership_id=user.tenant_id,
            kind=kind.value,
            actor_id=actor_id,
            auth_event_id=event.id,
        ),
    )
    return event


def recover(
    db: Session,
    mailer: PlatformMailer,
    *,
    user: User,
    action: RecoveryAction,
    reason: str,
    actor_id: uuid.UUID,
    ip_address: str,
) -> AuthEvent:
    """One of the four per-user actions, on a user row the caller locked
    (`get_user_for_recovery_or_404`). Does not commit."""

    reason = _clean_reason(reason)
    _assert_recoverable(user, actor_id=actor_id)
    details: dict = {}
    if action is RecoveryAction.REISSUE_INVITATION:
        # FR-AU-13's token rules: the open invitation ends, a fresh one is
        # mailed — the user sets the password, nobody else.
        identity.issue_invitation(db, mailer, user=user, actor_id=actor_id, ip_address=ip_address)
    elif action is RecoveryAction.RESET_MFA:
        mfa.remove_all_factors(db, user=user, actor_id=actor_id, ip_address=ip_address)
        # The sessions proved a factor that no longer exists; the next
        # sign-in enrols anew where the Betrieb (or D-A-36) enforces it.
        details["sessionsEnded"] = user_service.end_sessions(db, user=user, reason="mfa_removed", actor_id=actor_id)
    elif action is RecoveryAction.CLEAR_LOCKOUT:
        credential = db.scalar(select(Credential).where(Credential.user_id == user.id).with_for_update())
        if credential is None or (credential.failed_login_attempts == 0 and credential.locked_until is None):
            raise ConflictError("This account is not locked out.", details={"rule": "not_locked"})
        details["failedLoginAttempts"] = credential.failed_login_attempts
        credential.failed_login_attempts = 0
        credential.last_failed_at = None
        credential.locked_until = None
        credential.updated_by = actor_id
    elif action is RecoveryAction.REVOKE_SESSIONS:
        details["sessionsEnded"] = user_service.end_sessions(db, user=user, reason="platform_staff", actor_id=actor_id)
    return _record(
        db,
        user=user,
        kind=_EVENT_KIND[action],
        action=action.value,
        reason=reason,
        actor_id=actor_id,
        ip_address=ip_address,
        details=details,
    )


def grant_manager(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    user: User,
    reason: str,
    actor_id: uuid.UUID,
    actor_roles: frozenset[AccessRole],
    ip_address: str,
) -> AuthEvent:
    """FR-AU-25's fifth action: the manager flag for an existing active user
    of that Betrieb — at any time, with a reason (Anto, 2026-10-22; the spec
    named only the case of the last manager having left). The flag is set
    through the membership service, the same audit rows and the same ended
    sessions as a manager's grant. Commits (the membership service does)."""

    reason = _clean_reason(reason)
    if user.tenant_id != dealership_id:
        raise NotFoundError(f"User {user.id} was not found.")
    _assert_recoverable(user, actor_id=actor_id)
    if user.status != UserStatus.ACTIVE:
        raise ConflictError(
            "Only an active user can be made the Betrieb's manager.", details={"currentStatus": user.status.value}
        )
    if user.is_dealer_manager:
        raise ConflictError("This user is already a manager of the Betrieb.", details={"rule": "already_manager"})
    event = _record(
        db,
        user=user,
        kind=AuthEventKind.RECOVERY_MANAGER_GRANTED,
        action="grant-manager",
        reason=reason,
        actor_id=actor_id,
        ip_address=ip_address,
    )
    membership_service.set_home_manager_flag(
        db, user=user, is_dealer_manager=True, actor_id=actor_id, actor_roles=actor_roles
    )
    return event
