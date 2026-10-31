"""Staff notifications under `/v1/me` (KAN-216; D-A-40, FR-A-47…50): the
caller's own inbox, preferences and Web Push subscriptions."""

import datetime as dt
import uuid

from pydantic import ConfigDict, Field

from app.core.schemas import CamelModel
from app.platform.models.notification import NotificationFamily, NotificationTiming


class NotificationRead(CamelModel):
    """One inbox entry. The SPA renders `titleKey` / `bodyKey` with `params`
    in the user's UI language; `link` is the SPA path of the subject."""

    id: uuid.UUID
    event_code: str
    family: NotificationFamily
    title_key: str
    body_key: str
    params: dict[str, str | int | bool]
    subject_type: str
    subject_id: uuid.UUID | None
    link: str
    created_at: dt.datetime
    delivered_at: dt.datetime
    read_at: dt.datetime | None


class NotificationPage(CamelModel):
    items: list[NotificationRead]
    next_cursor: str | None


class UnreadCountRead(CamelModel):
    count: int


class NotificationReadUpdate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    read: bool


class MarkAllReadResult(CamelModel):
    marked: int


class NotificationPreferenceRead(CamelModel):
    family: NotificationFamily
    timing: NotificationTiming
    in_app: bool
    push: bool
    # False for `security`: always immediate, in-app and push (FR-A-48).
    switchable: bool
    # Whether any event of the family exists yet (only `security` in v1).
    has_events: bool
    # The If-Match for the next save; 0 while the platform default applies.
    version: int


class NotificationPreferenceList(CamelModel):
    items: list[NotificationPreferenceRead]


class NotificationPreferenceUpdate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    timing: NotificationTiming
    in_app: bool = True
    push: bool


class PushConfigRead(CamelModel):
    """Whether this deployment can send Web Push, and the VAPID application
    server key the browser subscribes with (null when it cannot)."""

    available: bool
    vapid_public_key: str | None


class PushSubscriptionKeys(CamelModel):
    model_config = ConfigDict(extra="forbid")

    # The browser's own spelling (`PushSubscription.toJSON().keys.p256dh`),
    # not the camelCase generator's `p256Dh`.
    p256dh: str = Field(alias="p256dh", min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=64)


class PushSubscriptionCreate(CamelModel):
    """The browser's `PushSubscription.toJSON()`: endpoint and keys
    (`expirationTime` is accepted and ignored)."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushSubscriptionKeys
    expiration_time: int | None = None


class PushSubscriptionRead(CamelModel):
    """Never the endpoint or the keys once stored."""

    id: uuid.UUID
    session_id: uuid.UUID
    created_at: dt.datetime
