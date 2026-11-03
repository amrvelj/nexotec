"""Vehicle's own outbox consumers.

Registered in app.worker.register_handlers: `sales.contract.invoice_requested`
as "vehicle.plate_on_invoice_requested" (KAN-523); and the five facts of a
host taking a configuration (KAN-270), each its own consumer of
`handle_configuration_taken_message` — below.

The plate event's contract, as Sales publishes it (`app/sales/services/contract.py
::request_invoice`; the contract is the aggregate, the delivering Betrieb the
`tenant_id`): `groupId` names the group whose register the plate is entered
in; `plate` and `canton` the plate the car leaves with, and `vehicleId` and
`vin` the car — all four null for «ohne Kontrollschild»; `moveFrom` the other cars'
assignments the user confirmed moving it from, each `{plateId, version}`;
`deliveryDate` (may be null) and `requestedOn` (the Swiss day invoicing was
requested) — the plate is valid from the first that is set; `requestedBy`
the user, the recording's actor.

The five configuration facts (KAN-270), each published by its host in the
transaction that stores the id, each carrying `configurationId` (absent
when the host row has none) in the host's Betrieb (`tenant_id`):
`sales.offer.configuration_attached` / `sales.contract.configuration_attached`
(with `slot`: `vehicle` | `trade_in`), `inventory.stock_item.added`,
`inventory.stock_item.configuration_attached` («Konfiguration zuordnen») and
`valuation.created`.
"""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.vehicle.services.configuration_retention import mark_configuration_taken
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


def handle_configuration_taken_message(db: Session, message: OutboxMessage) -> None:
    """KAN-270 — a host took a configuration: its `configurationId` is
    stamped taken (`first_taken_at`, the moment the host's fact occurred) in
    the host's Betrieb, the message's `tenant_id`; a configuration once taken
    is kept for good (`services/configuration_retention.py`). A fact without
    one (a stock item or valuation with no configuration, or published before
    KAN-270) stamps nothing, and so does a second fact for a configuration
    already stamped or one naming a configuration that is gone."""

    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    configuration_id = (message.payload or {}).get("configurationId")
    if not configuration_id:
        return
    mark_configuration_taken(
        db, tenant_id=message.tenant_id, configuration_id=uuid.UUID(configuration_id), taken_at=message.occurred_at
    )
