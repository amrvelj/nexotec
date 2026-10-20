"""UserConnectedAccount: an external account a user linked to their own
user record (KAN-201; Authentication & Identity FR-AU-23/24, Dealer
Administration D-A-15) — Microsoft 365 mailbox and calendar first. It is an
OAuth **grant** to act on the user's behalf, never an identity: it never
signs anyone in, never resolves a `user_id` and never appears in a session
token (`tests/architecture/test_connected_account_is_never_an_identity.py`
keeps the identity and session layer from reading it).

The row holds a reference into the secrets manager (`secret_ref`), never a
token. The linking flow ships with the Outlook integration; this ticket
builds the model, the user's and the manager's read and the revoke. A
manager sees provider, scopes and status and may revoke — never mail or
calendar content. Deactivating a user revokes every connected account.
A row is never deleted: a revoked grant keeps its row and stamps.
The manager's read is narrower than the user's: provider, scopes and
status (D-A-15, the ticket) — never the account's address or its usage
stamps, which say what an employee does with their mailbox.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ConnectedAccountProvider(str, enum.Enum):
    MICROSOFT_365 = "microsoft_365"


class ConnectedAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    # The provider refuses the grant (expired consent, password change at the
    # provider): it stays listed so the user re-links it.
    ERROR = "error"
    REVOKED = "revoked"


class ConnectedAccountRevokedReason(str, enum.Enum):
    USER = "user"  # the user themselves, in My account
    MANAGER = "manager"  # a manager, on the Users screen
    DEACTIVATED = "deactivated"  # the user was deactivated (FR-AU-24)


class UserConnectedAccount(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "user_connected_account"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "external_account_id", name="uq_user_connected_account_user_provider_external"
        ),
    )

    # Platform-owned on both ends: a real foreign key.
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    provider: Mapped[ConnectedAccountProvider] = mapped_column(
        StoredEnum(ConnectedAccountProvider, length=32), nullable=False
    )
    # The account at the provider (the Microsoft object id), for display and
    # de-duplication — not a credential.
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # A display label the provider gave (the mailbox address), shown to the
    # user only — the manager's read is provider, scopes and status.
    external_account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Where the grant's tokens live in the secrets manager — a reference,
    # never the secret (CLAUDE.md: no secrets in the application database).
    # Never returned by any endpoint.
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ConnectedAccountStatus] = mapped_column(
        StoredEnum(ConnectedAccountStatus, length=16), nullable=False, default=ConnectedAccountStatus.ACTIVE
    )
    linked_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    revoked_reason: Mapped[ConnectedAccountRevokedReason | None] = mapped_column(
        StoredEnum(ConnectedAccountRevokedReason, length=16), nullable=True
    )
