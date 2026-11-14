"""The integration context's outbox consumers (KAN-218). They run wherever
the outbox worker (`python -m app.worker`) runs; `app/worker.py::
register_handlers` lists them with the event type and `consumer_name`:

- `platform.dealership.offboarded` -> `integration.connections_disabled_on_offboarded`
  (`handle_dealership_offboarded_message`; Anto, 2026-11-14, Q3 A): every
  connection of the offboarded Betrieb disabled and its credentials revoked in
  the secrets manager (`services/connections.py::disable_offboarded_dealership_connections`).
  Platform states the fact and calls nothing here (ADR-131, rule 12); this
  consumer commits its own work with the processed-event row, so a
  redelivery is a no-op. Registered after Stock's withdrawal of the same
  fact, which still needs the marketplace connection for its empty feed.

Staging runs no worker (ADR-081): there the connections stay enabled until a
worker runs.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.integration.services.connections import disable_offboarded_dealership_connections


def handle_dealership_offboarded_message(db: Session, message: OutboxMessage) -> None:
    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"platform.dealership.offboarded message {message.id} names two Betriebe.")
    disable_offboarded_dealership_connections(db, tenant_id=dealership_id)
