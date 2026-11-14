"""Vehicle merge (WP-5 PR-6, FR-V-12) — one-way, audit-logged, no unmerge.
Same shape as the customer merge, WITH the re-pointing: plates,
accessories, odometer readings, custody events and Vehicle's own projection
of the records a Betrieb holds in stock move to the survivor here; the party
links (customer's `VehicleParty`) follow when customer consumes
`vehicle.vehicle_mdm.merged` (KAN-634, ADR-131: customer sits above vehicle,
so vehicle publishes the fact and never calls customer), and so do Stock's
items (`inventory.stock_items_on_vehicle_merged`, KAN-524).

`merge_vehicle_records` is the endpoint's entry (KAN-524; Anto, 2026-11-11,
-13 and -14): any user who may edit vehicles may merge any two records (Q1
B). It locks both rows, checks both versions and refuses a merge the caller's
own Betrieb could not carry: a live stock item on each record (Q4 A — another
Betrieb's double booking is never named; inventory's reconciliation reports it
to that Betrieb).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.audit import record_audit_event
from app.core.concurrency import check_version
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleCustodyEvent, VehicleOdometerReading
from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404

MERGED_EVENT = "vehicle.vehicle_mdm.merged"


def merge_vehicle_records(
    db: Session,
    *,
    duplicate_id: uuid.UUID,
    survivor_id: uuid.UUID,
    duplicate_version: int,
    survivor_version: int,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
) -> VehicleMdm:
    """`POST /v1/vehicle-mdm/{duplicate}/merge` (KAN-524, FR-V-12): folds the
    duplicate into the survivor for good.

    Both rows are locked in id order before anything is read from them, so two
    merges of one pair (or of A→B and B→A) serialise instead of crossing. Then,
    nothing written: the same record twice (422 `vehicle_merge_same_record`);
    a VIN record into a chassis-number record (422
    `vehicle_merge_vin_into_chassis_number`, KAN-535: the reverse is allowed);
    either version stale (409, If-Match for the duplicate, `survivorVersion`
    for the survivor); either already merged (409 `vehicle_merged`, naming the
    record it lives on); a live stock item of the caller's own Betrieb on each
    record (409 `vehicle_merge_both_in_stock` with both stock numbers — one of
    them is sold or cancelled first; Anto, Q4 A). Another Betrieb's stock never
    refuses and is never named (rule 7)."""

    from app.vehicle.services.stock_holding import held_stock_item

    if duplicate_id == survivor_id:
        raise UnprocessableEntityError(
            "A vehicle record cannot be merged into itself.",
            details={"field": "survivorId", "reason": "vehicle_merge_same_record"},
        )
    rows = {
        row.id: row
        for row in db.scalars(
            select(VehicleMdm)
            .where(VehicleMdm.id.in_(sorted((duplicate_id, survivor_id))))
            .order_by(VehicleMdm.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    }
    duplicate = get_vehicle_mdm_or_404(db, duplicate_id) if duplicate_id not in rows else rows[duplicate_id]
    survivor = get_vehicle_mdm_or_404(db, survivor_id) if survivor_id not in rows else rows[survivor_id]
    check_version(duplicate.version, duplicate_version, entity_name="VehicleMdm")
    check_version(survivor.version, survivor_version, entity_name="VehicleMdm")
    if (
        duplicate.identifier_kind == VehicleIdentifierKind.VIN
        and survivor.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER
    ):
        # KAN-535 (Anto, 2026-11-14, option B): a car bought under a VIN takes
        # no chassis number — but a chassis number may become a real VIN. So a
        # VIN record never folds into a chassis-number one (through Stock's
        # consumer it would hand the VIN car the chassis number); the other
        # direction is the way out, «Richtung tauschen» in the dialog.
        raise UnprocessableEntityError(
            "A VIN record cannot be merged into a pre-1981 chassis-number record; keep the VIN record instead.",
            details={"field": "survivorId", "reason": "vehicle_merge_vin_into_chassis_number"},
        )
    for record in (duplicate, survivor):
        if record.merged_into_vehicle_id is not None:
            raise ConflictError(
                f"Vehicle {record.vehicle_number} was already merged into another record.",
                details={
                    "reason": "vehicle_merged",
                    "vehicleId": str(record.id),
                    "mergedIntoVehicleId": str(resolve_merged_id(db, record.id)),
                },
            )
    held_duplicate = held_stock_item(db, tenant_id=tenant_id, vehicle_id=duplicate.id)
    held_survivor = held_stock_item(db, tenant_id=tenant_id, vehicle_id=survivor.id)
    if (
        held_duplicate is not None
        and held_survivor is not None
        and held_duplicate.stock_item_id != held_survivor.stock_item_id
    ):
        raise ConflictError(
            "Your Betrieb holds both records in stock: sell or cancel one stock car first.",
            details={
                "reason": "vehicle_merge_both_in_stock",
                "duplicateStockNumber": held_duplicate.stock_number,
                "survivorStockNumber": held_survivor.stock_number,
            },
        )
    return merge_vehicles(db, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=actor_id)


def merge_vehicles(db: Session, *, survivor_id: uuid.UUID, duplicate_id: uuid.UUID, actor_id: uuid.UUID) -> VehicleMdm:
    """Re-points every same-context reference (plates, accessories,
    odometer readings, custody events) in ONE local transaction, marks the
    duplicate merged, audits it and publishes `vehicle.vehicle_mdm.merged`
    in that same transaction (rule 4: business row and outbox row
    together), and commits.

    The party links are customer's rows: customer re-points them when it
    consumes the event (`customer.vehicle_parties_on_vehicle_merged`,
    KAN-634), in its own transaction — never in this one (ADR-047) and never
    by a call from here (ADR-131). Until the worker delivers it, the
    duplicate's holders still name the duplicate; nothing in this merge
    reads them. A delivery that fails is retried and then dead-lettered
    (rule 10), and nightly reconciliation reports a party row naming a
    merged vehicle.
    """

    survivor = get_vehicle_mdm_or_404(db, survivor_id)
    duplicate = get_vehicle_mdm_or_404(db, duplicate_id)
    if duplicate.merged_into_vehicle_id is not None:
        raise ValueError(f"Vehicle {duplicate_id} was already merged into {duplicate.merged_into_vehicle_id}.")

    repointed = {
        "vehicle_plate": _repoint(db, VehiclePlate, VehiclePlate.vehicle_id, duplicate_id, survivor_id),
        "vehicle_accessory": _repoint(db, VehicleAccessory, VehicleAccessory.vehicle_id, duplicate_id, survivor_id),
        "vehicle_odometer_reading": _repoint(
            db, VehicleOdometerReading, VehicleOdometerReading.vehicle_id, duplicate_id, survivor_id
        ),
        "vehicle_mdm_custody_event": _repoint(
            db, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id, duplicate_id, survivor_id
        ),
        # KAN-524: Vehicle's projection of the records a Betrieb holds follows
        # the car, as Stock's items do when it consumes the fact below, so the
        # nightly comparison of the two still lines up once both have moved.
        "vehicle_stock_holding": _repoint(
            db, VehicleStockHolding, VehicleStockHolding.vehicle_id, duplicate_id, survivor_id
        ),
    }

    duplicate.merged_into_vehicle_id = survivor_id
    duplicate.version += 1

    record_audit_event(
        db,
        entity_type="vehicle_mdm",
        entity_id=duplicate.id,
        tenant_id=None,
        action="merge",
        actor_id=actor_id,
        before={"mergedIntoVehicleId": None},
        after={"mergedIntoVehicleId": str(survivor_id), "repointed": repointed},
    )
    # vehicle-mdm is global (ADR-022): no Betrieb owns the fact, so
    # tenant_id is None and each consumer applies its own scope (customer
    # re-points every dealer group's party rows naming the duplicate).
    publish(
        db,
        OutboxEvent(
            event_type=MERGED_EVENT,
            tenant_id=None,
            producer="vehicle",
            aggregate_type="vehicle_mdm",
            aggregate_id=duplicate.id,
            payload={"vehicleId": str(duplicate.id), "mergedIntoVehicleId": str(survivor_id)},
        ),
    )
    db.commit()
    db.refresh(survivor)

    return survivor


def _repoint(
    db: Session,
    model: type[Any],
    vehicle_id_column: InstrumentedAttribute[uuid.UUID] | InstrumentedAttribute[uuid.UUID | None],
    duplicate_id: uuid.UUID,
    survivor_id: uuid.UUID,
) -> int:
    rows = list(db.scalars(select(model).where(vehicle_id_column == duplicate_id)).all())
    for row in rows:
        row.vehicle_id = survivor_id
    return len(rows)


def resolve_merged_id(db: Session, vehicle_id: uuid.UUID) -> uuid.UUID:
    """Follows merged_into_vehicle_id to the current survivor — a merge
    chain is possible (A merged into B, B later merged into C), so this
    walks until it finds an id that hasn't itself been merged away,
    guarding against a cycle that should never exist but must never hang
    if it somehow did.
    """

    # `seen |= {…}`, not `seen.add(…)`: the ADR-047 architecture test reads
    # any `.add` call as a session write, and this is a read
    # (`vehicle.public.resolve_merged_vehicle_id`, KAN-634).
    seen: set[uuid.UUID] = set()
    current_id = vehicle_id
    while current_id not in seen:
        seen |= {current_id}
        # The column, read from the database — never `db.get`, which answers
        # from the session's own copy of a row it loaded earlier: a party
        # write re-reads this under its lock to see a merge that committed
        # after the request first read the vehicle (KAN-673).
        merged_into = db.execute(
            select(VehicleMdm.merged_into_vehicle_id).where(VehicleMdm.id == current_id)
        ).one_or_none()
        if merged_into is None or merged_into[0] is None:
            return current_id
        current_id = merged_into[0]
    return current_id  # cycle guard — should be unreachable in practice


def follow_vehicle_merge_on_configuration(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    configuration_id: uuid.UUID,
    previous_vin: str,
    vin: str,
) -> bool:
    """KAN-524 (Anto, Q3 A): a stock car Stock moved onto a merge's survivor
    takes the survivor's VIN, and so does its configuration — Vehicle's row,
    written here on Vehicle's own session and committed before this returns
    (ADR-047), never inside Stock's transaction. The rules of «VIN korrigieren»
    hold (`configuration.follow_vehicle_vin_correction`): no VIN (a factory
    order) is left alone; `previous_vin` takes `vin`; already `vin` is only
    re-linked; a third VIN is 422 `configuration_vin_differs`, with nothing
    written. Audited `vin_corrected_by_merge` under the system actor. Compare-
    and-set, so a redelivered fact writes nothing a second time. Returns whether
    the configuration changed."""

    from app.vehicle.services.configuration import follow_vehicle_vin_correction

    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        vin_written, relinked = follow_vehicle_vin_correction(
            own,
            tenant_id=tenant_id,
            configuration_id=configuration_id,
            old_vin=previous_vin,
            new_vin=vin,
            reason="vehicle_merged",
            actor_id=None,
            action="vin_corrected_by_merge",
        )
        own.commit()
        return vin_written or relinked
