"""Vehicle's own outbox consumers.

Registered in app.worker.register_handlers: `sales.contract.invoice_requested`
as "vehicle.plate_on_invoice_requested" (KAN-523).

The event's contract, as Sales publishes it (`app/sales/services/contract.py
::request_invoice`; the contract is the aggregate, the delivering Betrieb the
`tenant_id`): `groupId` names the group whose register the plate is entered
in; `plate` and `canton` the plate the car leaves with, and `vehicleId` and
`vin` the car — all four null for «ohne Kontrollschild»; `moveFrom` the other cars'
assignments the user confirmed moving it from, each `{plateId, version}`;
`deliveryDate` (may be null) and `requestedOn` (the Swiss day invoicing was
requested) — the plate is valid from the first that is set; `requestedBy`
the user, the recording's actor.
"""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.vehicle.services.plate import PlateVersionRef
from app.vehicle.services.plate_handover import record_plate_on_invoice_requested


def handle_invoice_requested_message(db: Session, message: OutboxMessage) -> None:
    """A contract handed over without a plate (or published before KAN-523)
    records nothing."""

    if message.tenant_id is None:
        raise ValueError(f"sales.contract.invoice_requested message {message.id} has no tenant_id.")
    payload = message.payload or {}
    if not payload.get("plate"):
        return
    valid_from = payload.get("deliveryDate") or payload.get("requestedOn")
    if not valid_from:
        raise ValueError(f"sales.contract.invoice_requested message {message.id} carries a plate but no date.")
    requested_by = payload.get("requestedBy")
    record_plate_on_invoice_requested(
        db,
        tenant_id=message.tenant_id,
        group_id=uuid.UUID(payload["groupId"]),
        vehicle_id=uuid.UUID(payload["vehicleId"]),
        vin=payload.get("vin"),
        plate=payload["plate"],
        canton=payload["canton"],
        valid_from=dt.date.fromisoformat(valid_from),
        move_from=[PlateVersionRef(uuid.UUID(ref["plateId"]), int(ref["version"])) for ref in payload.get("moveFrom") or []],
        actor_id=uuid.UUID(requested_by) if requested_by else None,
    )
