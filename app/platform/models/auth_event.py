"""AuthEvent: the append-only authentication stream (FR-AU-20/21, KAN-196),
separate from the business audit log. Login success and failure, lockout,
throttling, password set and reset, invitation issued and accepted, MFA
enrolled and removed, recovery-code use, session revocation, credential
rehash, a breach check that could not be made, a platform mail that could
not be delivered. The dealer manager reads the stream for his own Betrieb
(`GET /v1/dealerships/{id}/auth-events`): "did someone try to get into
Marco's account?" and "who was in the system on Saturday?". Per-IP
throttling counts the recent failures here (FR-AU-08).

Rows are never updated or deleted by the application; `details` carries no
secret and no password-shaped value, ever.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class AuthEventKind(str, enum.Enum):
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGIN_THROTTLED = "login_throttled"
    LOCKED_OUT = "locked_out"
    PASSWORD_SET = "password_set"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET = "password_reset"
    INVITATION_ISSUED = "invitation_issued"
    INVITATION_ACCEPTED = "invitation_accepted"
    MFA_ENROLLED = "mfa_enrolled"
    MFA_REMOVED = "mfa_removed"
    MFA_FAILED = "mfa_failed"
    RECOVERY_CODE_USED = "recovery_code_used"
    RECOVERY_CODES_REGENERATED = "recovery_codes_regenerated"
    SESSION_REVOKED = "session_revoked"
    CREDENTIAL_REHASHED = "credential_rehashed"
    BREACH_CHECK_UNAVAILABLE = "breach_check_unavailable"
    MAIL_DELIVERY_FAILED = "mail_delivery_failed"
    # The address a user signs in and recovers with was changed (a manager
    # while the user is invited, platform staff afterwards): the old address
    # is told, tokens and sessions end (KAN-196).
    EMAIL_CHANGED = "email_changed"
    # A connected account (FR-AU-23/24, KAN-201) was revoked — by the user,
    # a manager or the deactivation; `details` names provider and reason.
    CONNECTED_ACCOUNT_REVOKED = "connected_account_revoked"
    # Platform staff's recovery actions (FR-AU-25/26, D-A-16, KAN-202): each
    # its own kind, the staff member the actor, the stated reason in
    # `details` — the Betrieb's manager reads it (Anto, 2026-10-22). The
    # mechanism's own events (`mfa_removed`, `session_revoked`,
    # `invitation_issued`) are recorded beside it, as for any other cause.
    RECOVERY_INVITATION_REISSUED = "recovery_invitation_reissued"
    RECOVERY_MFA_RESET = "recovery_mfa_reset"
    RECOVERY_LOCKOUT_CLEARED = "recovery_lockout_cleared"
    RECOVERY_SESSIONS_REVOKED = "recovery_sessions_revoked"
    RECOVERY_MANAGER_GRANTED = "recovery_manager_granted"
    # Moving an active user's sign-in address (Anto, 2026-10-22: recovery
    # too); `email_changed` is recorded beside it, as before.
    RECOVERY_EMAIL_CHANGED = "recovery_email_changed"


class AuthEvent(PrimaryKeyMixin, Base):
    __tablename__ = "auth_event"
    __table_args__ = (Index("ix_auth_event_ip_address_occurred_at", "ip_address", "occurred_at"),)

    kind: Mapped[AuthEventKind] = mapped_column(StoredEnum(AuthEventKind, length=32), nullable=False)
    # NULL when nobody could be named — a login attempt for an unknown
    # address records only the IP (FR-AU-16: the row must not say whether
    # the address exists either; the attempt is what it says).
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    dealership_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    # Who did it when it was not the user themselves (platform staff removing
    # a factor or re-issuing an invitation, a manager suspending). An actor column.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
