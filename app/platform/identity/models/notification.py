"""Staff notifications (KAN-216; Dealer Administration D-A-40, FR-A-47…50).

Four tables, all platform-owned:

- `notification_event` — **the catalogue** (FR-A-47). One row per event a
  module may raise: its family, its code, the timing it defaults to, whether
  the user may change that timing, and the i18n keys of its title and body.
  A module registers its events here in the same PR that raises them (the
  rows come with a migration of the platform chain; the declaration in
  `app/platform/identity/services/notification_catalogue.py` is the same list in
  code, and a test keeps the two equal). `notify()` refuses a code with no
  row: an event outside the catalogue cannot be sent.
- `user_notification_preference` — per user per family (FR-A-48): timing
  `immediate` · `digest` · `off`, and the channels `in_app` · `push`. No row
  means the platform default; nobody but the user writes it (never the
  manager). The `security` family is not switchable: immediate, both
  channels, always.
- `notification` — **the inbox, which is the record** (FR-A-50): every
  notification sent is a row, read or unread, with a link to its subject,
  retained 90 days. Push is a pointer to it, never the only copy.
- `push_subscription` — a browser's Web Push subscription (FR-A-49), bound
  to the session that registered it (FR-AU-17): a revoked session loses it.

Never an e-mail (D-A-40): nothing here names a mail sender.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, VersionedMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, EncryptedString, UTCDateTime
from app.db import Base


class NotificationFamily(str, enum.Enum):
    # Account and sign-in events about the user themselves (FR-AU-12, FR-AU-26).
    SECURITY = "security"
    # The Betrieb's health for its managers (FR-A-22): ships with the status page.
    ADMINISTRATION = "administration"
    # Something handed to me — ships with each module that hands things over.
    ASSIGNMENT = "assignment"


class NotificationTiming(str, enum.Enum):
    IMMEDIATE = "immediate"
    # Held back and released once a day by `platform.notifications.digest`.
    DIGEST = "digest"
    OFF = "off"


class NotificationEvent(PrimaryKeyMixin, Base):
    """One catalogue entry (FR-A-47). Written by migrations only."""

    __tablename__ = "notification_event"
    __table_args__ = (UniqueConstraint("code", name="uq_notification_event_code"),)

    # `<family>.<event>`, e.g. `security.new_device_login`.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[NotificationFamily] = mapped_column(StoredEnum(NotificationFamily, length=32), nullable=False)
    default_timing: Mapped[NotificationTiming] = mapped_column(
        StoredEnum(NotificationTiming, length=16), nullable=False
    )
    # False: the user's family preference does not apply — the event is
    # always delivered at its default timing on both channels (`security`).
    switchable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # i18n keys in the SPA's bundles (DE/FR/IT/EN); the push title is the
    # title key's text in the user's UI language.
    title_key: Mapped[str] = mapped_column(String(128), nullable=False)
    body_key: Mapped[str] = mapped_column(String(128), nullable=False)


class UserNotificationPreference(PrimaryKeyMixin, VersionedMixin, Base):
    """The user's choice for one family (FR-A-48). Absent = the platform
    default (immediate, in-app and push). If-Match on its version; the
    version an absent row is read at is 0."""

    __tablename__ = "user_notification_preference"
    __table_args__ = (UniqueConstraint("user_id", "family", name="uq_user_notification_preference_user_family"),)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    family: Mapped[NotificationFamily] = mapped_column(StoredEnum(NotificationFamily, length=32), nullable=False)
    timing: Mapped[NotificationTiming] = mapped_column(StoredEnum(NotificationTiming, length=16), nullable=False)
    # The inbox is the record (FR-A-50): a notification that is sent is
    # always stored, so `in_app` is true whenever the timing is not `off` —
    # the service refuses false. Kept as a column because the PRD names both
    # channels and a later channel rule must not need a migration.
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class Notification(PrimaryKeyMixin, Base):
    """One inbox entry (FR-A-50). Unversioned: the only change after it is
    written is the reader's own read/unread stamp."""

    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_user_id_created_at", "user_id", "created_at"),
        Index("ix_notification_created_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False)
    # The Betrieb the event happened in (the user's home for an account event).
    # Platform-owned: a real foreign key.
    dealership_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=True)
    event_code: Mapped[str] = mapped_column(String(64), ForeignKey("notification_event.code"), nullable=False)
    # The subject the notification is about and where the SPA opens it. The
    # subject may live in another context, so it is a plain id with its kind,
    # never a foreign key (rule 2); the link is an SPA path.
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    # Values the body template interpolates — closed codes only, never free
    # text a person typed, never a secret.
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    # When it reached the inbox: at once, or — for a family the user set to
    # `digest` — when the daily digest released it. NULL = still held.
    delivered_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PushSubscription(PrimaryKeyMixin, Base):
    """A browser's Web Push subscription (FR-A-49), bound to the session that
    registered it. Revoking the session deletes the row
    (`auth_sessions.revoke*`); the same browser registering again from a new
    session moves the row to that session (one row per endpoint)."""

    __tablename__ = "push_subscription"
    __table_args__ = (UniqueConstraint("endpoint_hash", name="uq_push_subscription_endpoint_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("session.id"), nullable=False, index=True)
    # The push service's URL for this browser — a capability URL, never
    # returned by the API once stored.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # The browser's keys (RFC 8291): the P-256 public key and the 16-byte
    # auth secret every message to it is encrypted with. Encrypted at rest.
    p256dh: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    auth: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    # A hash of the endpoint: the unique key (an encrypted or TEXT column
    # cannot carry a plain unique index of bounded size).
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
