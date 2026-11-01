"""The platform context's outbox consumers (KAN-216). They run wherever the
outbox worker (`python -m app.worker`) runs — not on staging (ADR-081), so
staging delivers the inbox but never a push. `app/worker.py::
register_handlers` lists them:

- `platform.notification.delivered` -> `platform.web_push_on_notification`
  (`handle_notification_delivered_message`): the notification's title and
  link to the user's live push subscriptions, when the family's push
  channel is on and the notification is still unread.
- `platform.notification_digest.released` -> `platform.web_push_on_digest`
  (`handle_digest_released_message`): one pointer to the inbox for the
  day's digest, when any of its families pushes.

A push is a pointer, never the record (FR-A-50), and never an e-mail
(D-A-40).
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.platform.models.notification import Notification, NotificationFamily
from app.platform.services import notification_catalogue, notifications, push_subscriptions
from app.platform.services.web_push import PushMessage


def handle_notification_delivered_message(db: Session, message: OutboxMessage) -> None:
    notification = db.get(Notification, uuid.UUID(message.payload["notificationId"]))
    if notification is None or notification.read_at is not None:
        # Purged, or already read on another device: nothing to point at.
        return
    event = notifications.catalogue_event(db, notification.event_code)
    if not notifications.effective_preference(db, user_id=notification.user_id, event=event).push:
        return
    language = notifications.ui_language_of(db, notification.user_id)
    push_subscriptions.push_to_user(
        db,
        user_id=notification.user_id,
        message=PushMessage(
            title=notification_catalogue.push_title(notification.event_code, language), link=notification.link
        ),
    )


def handle_digest_released_message(db: Session, message: OutboxMessage) -> None:
    user_id = uuid.UUID(message.payload["userId"])
    families = [NotificationFamily(value) for value in message.payload["families"]]
    if not any(notifications.family_push_enabled(db, user_id=user_id, family=family) for family in families):
        return
    language = notifications.ui_language_of(db, user_id)
    push_subscriptions.push_to_user(
        db,
        user_id=user_id,
        message=PushMessage(
            title=notification_catalogue.digest_push_title(language), link=notification_catalogue.INBOX_LINK
        ),
    )
