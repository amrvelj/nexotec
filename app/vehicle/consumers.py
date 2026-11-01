"""The vehicle context's outbox consumers. They run wherever the outbox
worker (`python -m app.worker`) is provisioned — not on staging (ADR-081);
`app/worker.py::register_handlers` is the list of what is registered:

- `sales.contract.delivered` -> `vehicle.plate_on_contract_delivered`
  (`handle_sales_contract_delivered_message`, KAN-467): a sold car delivered
  with its Kontrollschild is recorded in the group's plate register — the
  second of Anto's three ways a plate gets in (2026-10-26), through the same
  `services/plate.py::record_plate` the Plates tab uses.

The event contract (`sales.contract.delivered`, version 1) is fixed here, in
the consumer, because Sales has no delivery step yet (KAN-467 criterion 3;
the Sales side is its own follow-up ticket). Sales emits it when a contract's
delivery is recorded, with the contract as the aggregate, the delivering
Betrieb as `tenant_id`, and this payload:

    contractNumber    str
    vehicleId         the vehicle-mdm id of the delivered car
    groupId           the delivering Betrieb's dealer group (the register)
    deliveredOn       ISO date — the plate's first day on the car
    deliveredBy       the user who recorded the delivery, or null
    plate, canton     the Kontrollschild, or both null for a car delivered
                      without one (a dealer plate, an export)
    moveFromPlateIds  the assignments the user confirmed to move (the
                      plate was current on another car of the group); []

A plate already recorded on this car for that day (the Plates tab got there
first) is no new fact and the delivery records nothing. Any other refusal —
the plate is on another car the user did not confirm, because the register
changed between the confirmation and the delivery — fails the delivery: the
worker retries it and dead-letters it with an alert (rule 10), and a person
resolves it on the Plates tab. A consumer never decides a move on its own.
"""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.vehicle.services.plate import record_plate

CONTRACT_DELIVERED_EVENT = "sales.contract.delivered"


def handle_sales_contract_delivered_message(db: Session, message: OutboxMessage) -> None:
    if message.tenant_id is None:
        raise ValueError(f"{CONTRACT_DELIVERED_EVENT} message {message.id} has no tenant_id.")
    payload = message.payload
    if not payload.get("plate"):
        return
    delivered_by = payload.get("deliveredBy")
    try:
        record_plate(
            db,
            vehicle_id=uuid.UUID(payload["vehicleId"]),
            group_id=uuid.UUID(payload["groupId"]),
            recording_tenant_id=message.tenant_id,
            actor_id=uuid.UUID(delivered_by) if delivered_by else None,
            plate=payload["plate"],
            canton=payload["canton"],
            valid_from=dt.date.fromisoformat(payload["deliveredOn"]),
            move_from_plate_ids=[uuid.UUID(i) for i in payload.get("moveFromPlateIds") or []],
        )
    except ConflictError as exc:
        if (exc.details or {}).get("reason") == "plate_already_on_vehicle":
            db.rollback()
            return
        raise
