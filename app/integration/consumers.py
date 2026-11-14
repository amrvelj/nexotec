"""The integration context's outbox consumers (KAN-218). They run wherever
the outbox worker (`python -m app.worker`) runs; `app/worker.py::
register_handlers` lists them with the event type and `consumer_name`:

- `platform.dealership.offboarded` -> `integration.connections_disabled_on_offboarded`
  (`handle_dealership_offboarded_message`; Anto, 2026-11-14, Q3 A): every
  connection of the offboarded Betrieb but the ones Stock publishes listings
  through disabled and its credentials revoked in the secrets manager
  (`services/connections.py::disable_offboarded_dealership_connections`).
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


def _dealership_id(message: OutboxMessage) -> uuid.UUID:
    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"{message.event_type} message {message.id} names two Betriebe.")
    return dealership_id


def handle_dealership_offboarded_message(db: Session, message: OutboxMessage) -> None:
    disable_offboarded_dealership_connections(db, tenant_id=_dealership_id(message))


def handle_dealership_listings_withdrawn_message(db: Session, message: OutboxMessage) -> None:
    disable_withdrawn_marketplace_connections(db, tenant_id=_dealership_id(message))
