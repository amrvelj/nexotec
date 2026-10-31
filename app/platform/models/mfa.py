"""Second factors (D-A-09, D-A-36, A-13, KAN-196): a TOTP secret (encrypted at
rest with the platform Fernet key), ten single-use recovery codes (hashed —
a code in a database is a password), and passkeys (WebAuthn resident
credentials: public key, credential id, sign counter). A user has at most one
TOTP enrolment; a factor counts only once confirmed.
"""

import datetime as dt
import uuid

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, EncryptedString, UTCDateTime
from app.db import Base


class MfaTotp(PrimaryKeyMixin, Base):
    __tablename__ = "mfa_totp"

    # KAN-431: the migration's constraint name, not SQLAlchemy's default
    # `{table}_{column}_key` — the schema gate compares indexes by name.
    __table_args__ = (UniqueConstraint("user_id", name="uq_mfa_totp_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False)
    secret: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # RFC 6238 replay guard: the last time step a code was accepted for.
    last_used_step: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MfaRecoveryCode(PrimaryKeyMixin, Base):
    __tablename__ = "mfa_recovery_code"

    # KAN-431: the migration's constraint name, not SQLAlchemy's default
    # `{table}_{column}_key` — the schema gate compares indexes by name.
    __table_args__ = (UniqueConstraint("code_hash", name="uq_mfa_recovery_code_code_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MfaPasskey(PrimaryKeyMixin, Base):
    __tablename__ = "mfa_passkey"

    # KAN-431: the migration's constraint name, not SQLAlchemy's default
    # `{table}_{column}_key` — the schema gate compares indexes by name.
    __table_args__ = (UniqueConstraint("credential_id", name="uq_mfa_passkey_credential_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    # base64url of the authenticator's credential id; unique across users.
    # A credential id is up to 1023 bytes (WebAuthn L2 §5.8.3): 1364 characters base64url.
    credential_id: Mapped[str] = mapped_column(String(1364), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)  # base64url COSE key
    # The authenticator's signature counter is unsigned 32-bit; a signed Integer
    # would refuse a valid counter past 2^31 (twelfth review).
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
