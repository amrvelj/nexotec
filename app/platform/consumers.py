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
- `platform.connected_account.revoked` ->
  `platform.connected_account_provider_revocation`
  (`handle_connected_account_revoked_message`, KAN-412): the provider's
  registered revoker ends the grant at the provider; a refusal raises, so
  the outbox redelivers and finally dead-letters.

- `platform.data_export.requested` -> `platform.data_export_production`
  (`handle_data_export_requested_message`, KAN-218): the export's file is
  written into the object store; a redelivery finds the export no longer
  `requested` and does nothing.

- `integration.connection.expiry_approaching` ->
  `platform.administration_notification_on_connection_expiry`
  (`handle_connection_expiry_approaching_message`, KAN-222, FR-A-22): one
  `administration.integration_expiring` notification to each active manager
  of the Betrieb. Integration states the fact and calls nothing here
  (ADR-047); the inbox rows commit with the processed-event row, so a
  redelivery raises nothing twice.

A push is a pointer, never the record (FR-A-50), and never an e-mail
(D-A-40).
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.platform.identity.models.notification import Notification, NotificationFamily
from app.platform.identity.services import (
    connected_account,
    notification_catalogue,
    notifications,
    push_subscriptions,
    user_reads,
)
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.reference.services.web_push import PushMessage
from app.platform.services import data_export


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


def handle_connected_account_revoked_message(db: Session, message: OutboxMessage) -> None:
    """The revoke committed; now the provider (KAN-412). The payload names
    the grant; its reference is read from the row by its own service. A
    revoker's exception is left to propagate — that is the retry."""

    connected_account.end_at_provider(db, account_id=uuid.UUID(message.payload["connectedAccountId"]))


def handle_connection_expiry_approaching_message(db: Session, message: OutboxMessage) -> None:
    """KAN-222: integration's fact that a connection expires in T-30/14/7
    days becomes the managers' `administration` notification. The payload is
    trusted for ids, the provider's code and name and the threshold only."""

    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"{message.event_type} message {message.id} names two Betriebe.")
    connection_id = uuid.UUID(message.payload["connectionId"])
    for user_id in user_reads.list_dealer_manager_ids(db, dealership_id=dealership_id):
        notifications.notify(
            db,
            NotificationEventCode.INTEGRATION_EXPIRING,
            user_id=user_id,
            subject=notifications.NotificationSubject(
                type="integration_connection", id=connection_id, link="/administration/integrations"
            ),
            dealership_id=dealership_id,
            params={
                "days": int(message.payload["thresholdDays"]),
                # A message written before the name joined the payload names the code.
                "provider": str(message.payload.get("providerName") or message.payload["providerCode"])[:64],
            },
        )


def handle_data_export_requested_message(db: Session, message: OutboxMessage) -> None:
    """KAN-218 (FR-A-44): produce the requested export. Idempotent by the
    export's own status, beside the harness's processed-event record."""

    data_export.produce(db, export_id=uuid.UUID(message.payload["exportId"]))
