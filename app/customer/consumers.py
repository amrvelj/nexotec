"""Customer's own outbox consumers.

Registered in app.worker.register_handlers: `vehicle.vehicle_mdm.summary_changed`
as "customer.vehicle_party_labels_on_summary_changed" (KAN-186).

The event's contract, as vehicle-mdm publishes it
(`app/vehicle/services/vehicle_mdm.py::publish_summary_changed_if_changed`;
the vehicle-mdm record is the aggregate, `tenant_id` is null — the record is
global, ADR-022): `vehicleId`, the record's `version` after the write,
`changedFields` (camelCase names of the summary fields that changed) and
`summary` (`vin`, `vehicleNumber`, `make`, `model`, `modelYear`, `trim` as
they now stand).
"""

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.customer.services.customer import relabel_vehicle_parties_for_vehicle


def handle_vehicle_summary_changed_message(db: Session, message: OutboxMessage) -> None:
    """KAN-186 — every party row naming the vehicle takes its current label
    (CLAUDE.md rule 2's three-column pattern, KAN-84). The event says which
    vehicle; the label itself is re-read through `app.vehicle.public`, so a
    late or reordered delivery writes the current label, never an older one.
    Flushes only; consume_once commits it together with the processed_event
    row. A vehicle no party row names changes nothing."""

    relabel_vehicle_parties_for_vehicle(db, vehicle_id=message.aggregate_id)
