"""The integration context's outbox consumers (KAN-218). They run wherever
the outbox worker (`python -m app.worker`) runs; `app/worker.py::
register_handlers` lists them with the event type and `consumer_name`:

- `platform.dealership.offboarded` -> `integration.connections_disabled_on_offboarded`
  (`handle_dealership_offboarded_message`; Anto, 2026-11-14, Q3 A): every
  connection of the offboarded Betrieb but the ones Stock publishes listings
  through disabled and its credentials revoked in the secrets manager
  (`services/connections.py::disable_offboarded_dealership_connections`),
  every mailbox of the Betrieb ended (`services/mail_sending.py::
  disconnect_offboarded_dealership_mailboxes`, KAN-215), and every live
  Google Business Profile binding ended — its secret dropped, never revoked
  at Google (KAN-774) (`services/gbp_link.py::
  disconnect_offboarded_dealership_bindings`, KAN-226).
- `inventory.dealership_listings.withdrawn` ->
  `integration.marketplace_connections_disabled_on_withdrawn`
  (`handle_dealership_listings_withdrawn_message`): Stock's fact that it has
  withdrawn the offboarded Betrieb's listings — only then are the connections
  it withdrew through disabled and revoked
  (`disable_withdrawn_marketplace_connections`). Facts, not delivery order:
  the transport delivers to each consumer independently, at least once and
  unordered, so a Stock delivery that failed once must still find its
  connection enabled on the retry. Should Stock dead-letter, the connections
  stay enabled and the dead-letter alert fires (rule 10).

- `platform.location.updated` -> `integration.business_profile_push_on_location_updated`,
  `platform.location.schedule_changed` -> `integration.business_profile_push_on_schedule_changed`,
  `integration.gbp_location_binding.activated` -> `integration.business_profile_push_on_binding_activated`
  (`handle_business_profile_push_message`, KAN-226 PR-3): the location's
  fields, hours and closed days pushed to its live Google Business Profile
  binding, once per state (`services/gbp_push.py::push_location`).
- `platform.publishing_profile.updated` -> `integration.business_profile_photos_on_profile_updated`,
  `integration.gbp_location_binding.activated` -> `integration.business_profile_photos_on_binding_activated`
  (`handle_business_profile_photos_message`): the location's publishing
  images not yet uploaded to the binding (`gbp_push.sync_photos`). Its own
  consumers, so a photo that cannot be read never holds the hours back.

Platform and Stock state their facts and call nothing here (ADR-131, rule
12); each consumer commits its own work with the processed-event row, so a
redelivery is a no-op. Staging runs no worker (ADR-081): there the
connections stay enabled until a worker runs.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.integration.services.connections import (
    disable_offboarded_dealership_connections,
    disable_withdrawn_marketplace_connections,
)
from app.integration.services.gbp_link import disconnect_offboarded_dealership_bindings
from app.integration.services.gbp_push import push_location, sync_photos
from app.integration.services.mail_sending import disconnect_offboarded_dealership_mailboxes


def _dealership_id(message: OutboxMessage) -> uuid.UUID:
    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"{message.event_type} message {message.id} names two businesses.")
    return dealership_id


def handle_dealership_offboarded_message(db: Session, message: OutboxMessage) -> None:
    dealership_id = _dealership_id(message)
    disable_offboarded_dealership_connections(db, tenant_id=dealership_id)
    disconnect_offboarded_dealership_mailboxes(db, tenant_id=dealership_id)
    disconnect_offboarded_dealership_bindings(db, tenant_id=dealership_id)


def handle_dealership_listings_withdrawn_message(db: Session, message: OutboxMessage) -> None:
    disable_withdrawn_marketplace_connections(db, tenant_id=_dealership_id(message))


def handle_business_profile_push_message(db: Session, message: OutboxMessage) -> None:
    push_location(db, message)


def handle_business_profile_photos_message(db: Session, message: OutboxMessage) -> None:
    sync_photos(db, message)
