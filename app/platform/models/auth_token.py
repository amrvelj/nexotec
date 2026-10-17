"""AuthToken: the single-use tokens of invitation (14 days), password reset
(1 hour) and the MFA step of a login (5 minutes) — FR-AU-13/14, KAN-196.
A token in a database is a password: only its SHA-256 is stored, the raw
value leaves once, in the e-mail or the login response. One active token per
(user, kind): issuing a new one consumes the previous. Consumed atomically
on first use (one UPDATE … WHERE consumed_at IS NULL), so two concurrent
presentations cannot both succeed.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class AuthTokenKind(str, enum.Enum):
    INVITATION = "invitation"
    PASSWORD_RESET = "password_reset"
    MFA_CHALLENGE = "mfa_challenge"
    # A passkey registration from My account (its own kind, so a login
    # challenge never doubles as a registration challenge or vice versa).
    PASSKEY_REGISTRATION = "passkey_registration"


class AuthToken(PrimaryKeyMixin, Base):
    __tablename__ = "auth_token"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    kind: Mapped[AuthTokenKind] = mapped_column(StoredEnum(AuthTokenKind, length=32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Who issued it: the inviting manager, platform staff on a recovery, or
    # NULL for a self-service reset request. An actor column (KAN-243).
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    def is_live(self, at: dt.datetime) -> bool:
        return self.consumed_at is None and self.expires_at > at
