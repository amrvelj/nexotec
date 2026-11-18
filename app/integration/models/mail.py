"""Customer mail through the dealer's own mailbox (KAN-215; D-A-28 as re-cut by
Anto on 2026-11-14): customer-facing mail leaves only through a Microsoft 365
or Google Workspace mailbox the Betrieb or the user connected. No Nexotec
sender, no paid mail provider.

- `MailLinkState` — one pending link (the OAuth round trip). Only the SHA-256
  of the single-use `state` is stored, bound to the user, the Betrieb, the kind
  and the provider; it lives ten minutes. The PKCE verifier and the OIDC nonce
  are derived from the state (HKDF, `services/mail_link.py`), never stored. A
  business-mailbox link has two legs (the admin's OIDC sign-in, then the
  admin consent): the verified provider tenant of the first leg is written on
  the row and the second leg never trusts the browser's copy of it (F1).
- `SendingIdentity` — a connected mailbox: `user_mailbox` (the author's own,
  a delegated grant whose token lives in the secrets manager and whose grant
  is platform's connected-account row, FR-AU-23 — this row only names
  it) or `business_mailbox` (the Betrieb's shared mailbox, sent as by admin
  consent: Microsoft's application permission `Mail.Send`, Google's
  domain-wide delegation; no token is held per mailbox). No secret here.
- `MessageDelivery` — one row per send, written on integration's own session
  (ADR-047), success or failure.

Platform's ids (`tenant_id` = Dealership.id, user ids, the connected account
id) are plain GUIDs with an owner comment, never a foreign key (rule 2). The
author's and the identity owner's names are not copied: nothing here shows
them.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class MailProviderCode(str, enum.Enum):
    """The two mailbox providers (`integration_provider.provider_code`)."""

    MICROSOFT_365 = "microsoft_365_mail"
    GOOGLE_WORKSPACE = "google_workspace_mail"


class SendingIdentityKind(str, enum.Enum):
    USER_MAILBOX = "user_mailbox"
    BUSINESS_MAILBOX = "business_mailbox"


class SendingIdentityStatus(str, enum.Enum):
    ACTIVE = "active"
    # The provider refused a send or a token refresh with an answer that
    # needs the owner (consent withdrawn, mailbox gone): reconnect.
    ERROR = "error"
    DISCONNECTED = "disconnected"


class MailLinkLeg(str, enum.Enum):
    # A user mailbox: one authorisation-code round trip with PKCE.
    USER_SIGNIN = "user_signin"
    # A business mailbox: the admin's OIDC sign-in first, then the consent.
    ADMIN_SIGNIN = "admin_signin"
    ADMIN_CONSENT = "admin_consent"


class DeliveryStatus(str, enum.Enum):
    SENT = "sent"
    FAILED = "failed"


class MailLinkState(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_mail_link_state"

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    kind: Mapped[SendingIdentityKind] = mapped_column(StoredEnum(SendingIdentityKind, length=24), nullable=False)
    provider_code: Mapped[MailProviderCode] = mapped_column(StoredEnum(MailProviderCode, length=32), nullable=False)
    leg: Mapped[MailLinkLeg] = mapped_column(StoredEnum(MailLinkLeg, length=16), nullable=False)
    # A business mailbox: the address the manager asked to connect.
    mailbox_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Set by the verified admin sign-in (leg 1): the Microsoft `tid` or the
    # Google Workspace domain (`hd`). Never taken from the browser.
    provider_tenant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SendingIdentity(PrimaryKeyMixin, VersionedMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "integration_sending_identity"
    __table_args__ = (
        # One live shared mailbox per Betrieb.
        Index(
            "uq_integration_sending_identity_business_live",
            "tenant_id",
            unique=True,
            postgresql_where=text("kind = 'business_mailbox' AND status <> 'disconnected'"),
            sqlite_where=text("kind = 'business_mailbox' AND status <> 'disconnected'"),
        ),
        # One live own mailbox per user and Betrieb.
        Index(
            "uq_integration_sending_identity_user_live",
            "tenant_id",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'user_mailbox' AND status <> 'disconnected'"),
            sqlite_where=text("kind = 'user_mailbox' AND status <> 'disconnected'"),
        ),
    )

    kind: Mapped[SendingIdentityKind] = mapped_column(StoredEnum(SendingIdentityKind, length=24), nullable=False)
    provider_code: Mapped[MailProviderCode] = mapped_column(StoredEnum(MailProviderCode, length=32), nullable=False)
    # A user mailbox's owner; NULL for the Betrieb's shared mailbox.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    # platform's connected-account row holding the grant (a user
    # mailbox only; FR-AU-23).
    connected_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
        comment="Owned by the platform context (its connected account id, FR-AU-23). No DB-level FK.",
    )
    mailbox_address: Mapped[str] = mapped_column(String(320), nullable=False)
    # The account at the provider (Microsoft object id, Google subject).
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # The Microsoft tenant id or the Google Workspace domain the mailbox lives in.
    provider_tenant: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[SendingIdentityStatus] = mapped_column(
        StoredEnum(SendingIdentityStatus, length=16), nullable=False, default=SendingIdentityStatus.ACTIVE
    )
    # A closed code when `status` is error (`consent_revoked`, `mailbox_not_found`, ...).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    disconnected_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MessageDelivery(PrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "integration_message_delivery"

    sending_identity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_sending_identity.id"), nullable=False, index=True
    )
    identity_kind: Mapped[SendingIdentityKind] = mapped_column(
        StoredEnum(SendingIdentityKind, length=24), nullable=False
    )
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    # The registered document type that was sent (`offer`, ...) and the
    # owning module's document id — plain values, no foreign key.
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the sending module (its document id). No DB-level FK."
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    status: Mapped[DeliveryStatus] = mapped_column(StoredEnum(DeliveryStatus, length=16), nullable=False)
    # A closed code when failed (`provider_unavailable`, `provider_refused`, ...); never the provider's text.
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
