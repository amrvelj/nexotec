"""«VIN korrigieren» on a car in stock (KAN-557; ADR-116 (2)-(3)).

Anto, 2026-11-03, on KAN-544: «wir lassen eine Korrektur am Lager zu. Es
können immer Fehler geschehen». Vehicle owns the VIN (rule 1), so Stock's
correction asks here, through `app.vehicle.public.correct_vehicle_vin`,
before it writes its own row (ADR-047: a call with a compensating action —
the compensation is the same call with the two VINs swapped).

What this changes, in **one transaction of Vehicle's own** (a session bound
to the caller's database; the caller's pending work is neither committed nor
rolled back, as `vin_resolution.py`):

- the vehicle-mdm record's VIN, audited `vin_corrected` (or
  `vin_correction_reverted` for the compensation) with the reason, and
  published as `vehicle.vehicle_mdm.vin_corrected` plus the ADR-119
  `vehicle.vehicle_mdm.summary_changed` (the VIN is a summary field, so
  customer's `VehicleParty` labels follow by vehicle id);
- when the caller names one, the stock car's configuration: its VIN follows
  the record's, and its link is re-derived from it. Both tables are
  Vehicle's, so this is one context's write; ADR-070 is untouched — a
  configuration still never writes vehicle-mdm, here vehicle-mdm's own
  correction carries the configuration along.

**Compare and set.** The record must still carry `old_vin`; a record already
carrying `new_vin` is the correction already made (a retry after the
caller's own write failed) and nothing is written again. Any third VIN is
409 `vehicle_vin_changed`: someone else changed it meanwhile.

**A pre-1981 car's chassis number** (KAN-535; Anto, 2026-11-14, option B) is
corrected here too, under the same rules: the record stays a chassis-number
record with its make key and its first registration (the CHECK keeps that
before 1981), and the record it would collide with is another of the same
make key — the number alone may be another make's car. A chassis number
corrected to a real ISO 3779 VIN makes it a VIN record, its make key dropped
as every VIN record's is: the existing rule that a valid VIN is a VIN
(`is_chassis_number`). A VIN record never takes a chassis number here (422
`invalid_vin`, as before), except in the compensation of that conversion,
which restores the make key the answer reported.

This module imports nothing of Vehicle's services at load time, for the
reason `vin_resolution.py` gives.
"""

import dataclasses
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.validators import (
    PRE_ISO_VIN_CUTOFF,
    is_chassis_number,
    is_valid_vin,
    require_vin_or_pre_1981_chassis_number,
)

if TYPE_CHECKING:
    from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm

VIN_CORRECTED_EVENT = "vehicle.vehicle_mdm.vin_corrected"


@dataclasses.dataclass(frozen=True)
class CorrectedVehicleVin:
    """What the correction changed — plain data, never the row.
    `record_changed` is False when the record already carried `new_vin`;
    `configuration_vin_changed` says this call wrote the configuration's
    VIN, `configuration_relinked` that it only re-derived its link (it
    already carried `new_vin`). The compensation reverts what these say —
    a re-linked configuration is re-linked, never given the old VIN back.
    `previous_chassis_make_key` is the make key of a chassis-number record
    this call turned into a VIN record (KAN-535) — what its compensation
    restores; None otherwise."""

    vehicle_id: uuid.UUID
    vehicle_number: str
    vin: str
    previous_vin: str
    record_changed: bool
    configuration_vin_changed: bool
    configuration_relinked: bool
    previous_chassis_make_key: str | None = None

    @property
    def configuration_changed(self) -> bool:
        return self.configuration_vin_changed or self.configuration_relinked


def correct_vehicle_vin(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    old_vin: str,
    new_vin: str,
    reason: str,
    actor_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
    configuration_id: uuid.UUID | None = None,
    compensation: bool = False,
    include_record: bool = True,
    relink_configuration_only: bool = False,
    restore_chassis_make_key: str | None = None,
) -> CorrectedVehicleVin:
    """Rewrites the vehicle-mdm record's VIN from `old_vin` to `new_vin`
    (both valid and normalised — a VIN, or on a chassis-number record a
    pre-1981 car's chassis number, KAN-535) and, when `configuration_id` is
    given, has that configuration of `tenant_id` follow — committed before
    this returns, on Vehicle's own session.

    `compensation=True` marks the call as the undo of an earlier one (the
    VINs swapped): its audit rows read `vin_correction_reverted`. With
    `include_record=False` (the compensation of a call that changed only the
    configuration) the record is left exactly as it is; with
    `relink_configuration_only=True` (the compensation of a configuration
    that was only re-linked) the configuration keeps its VIN and only its link
    is derived again. `restore_chassis_make_key` (read only with
    `compensation=True`) undoes a chassis number corrected to a VIN: the
    record is the chassis-number record it was, with that make key.

    Refused, with nothing written: the record is unknown (404) or merged
    into another (409 `vehicle_merged`); it no longer carries `old_vin`
    (409 `vehicle_vin_changed`); `new_vin` already names another record
    (409 `vin_belongs_to_other_vehicle` with its `vehicleNumber`: the two
    records are one car, merged with «Zusammenführen mit…», FR-V-12); a VIN
    record is given a chassis number (422 `invalid_vin`); the configuration
    is not the tenant's (404), names a third VIN (422
    `configuration_vin_differs`) or, given a chassis number, is not first
    registered before 1981 (422 `chassis_number_requires_pre_1981_registration`)."""

    from app.vehicle.models.vehicle_mdm import VehicleMdm
    from app.vehicle.services.configuration import follow_vehicle_vin_correction

    action = "vin_correction_reverted" if compensation else "vin_corrected"
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        vehicle = own.scalar(select(VehicleMdm).where(VehicleMdm.id == vehicle_id).with_for_update(key_share=True))
        if vehicle is None:
            raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
        make_key_before = vehicle.chassis_make_key
        record_changed = False
        if include_record:
            record_changed = _correct_record(
                own,
                vehicle=vehicle,
                old_vin=old_vin,
                new_vin=new_vin,
                reason=reason,
                actor_id=actor_id,
                action=action,
                compensation=compensation,
                restore_chassis_make_key=restore_chassis_make_key if compensation else None,
            )
        configuration_vin_changed = configuration_relinked = False
        if configuration_id is not None:
            configuration_vin_changed, configuration_relinked = follow_vehicle_vin_correction(
                own,
                tenant_id=tenant_id,
                configuration_id=configuration_id,
                old_vin=old_vin,
                new_vin=old_vin if relink_configuration_only else new_vin,
                reason=reason,
                actor_id=actor_id,
                action=action,
            )
            if configuration_vin_changed and not compensation:
                _require_configuration_takes(own, configuration_id=configuration_id, new_vin=new_vin)
        own.commit()
        return CorrectedVehicleVin(
            vehicle_id=vehicle.id,
            vehicle_number=vehicle.vehicle_number,
            vin=vehicle.vin,
            previous_vin=old_vin,
            record_changed=record_changed,
            configuration_vin_changed=configuration_vin_changed,
            configuration_relinked=configuration_relinked,
            previous_chassis_make_key=(
                make_key_before if record_changed and vehicle.chassis_make_key is None else None
            ),
        )


def _require_configuration_takes(own: Session, *, configuration_id: uuid.UUID, new_vin: str) -> None:
    """KAN-535: a chassis number written onto the configuration keeps the
    configuration's own rule (KAN-353) — a first registration before 1981 —
    else 422 `chassis_number_requires_pre_1981_registration`, raised before
    the commit so the whole correction (record included) rolls back. The
    compensation puts the old value back whatever the date says by now."""

    from app.vehicle.models.configuration import VehicleConfiguration

    registered = own.scalar(
        select(VehicleConfiguration.first_registration_date).where(VehicleConfiguration.id == configuration_id)
    )
    try:
        require_vin_or_pre_1981_chassis_number(new_vin, registered)
    except UnprocessableEntityError as exc:
        raise UnprocessableEntityError(exc.message, details={**(exc.details or {}), "field": "newVin"}) from None


def find_other_record_carrying(db: Session, *, vehicle_id: uuid.UUID, new_vin: str) -> "VehicleMdm | None":
    """Another record already carrying `new_vin` as this one would carry it
    once corrected — a VIN record by its VIN, a chassis-number record by its
    number and this record's make key (KAN-535). A read on the caller's
    session, for Stock's refusal before anything is written; None when there
    is none, or when this record is unknown or would refuse the value."""

    from app.vehicle.models.vehicle_mdm import VehicleMdm

    vehicle = db.get(VehicleMdm, vehicle_id)
    if vehicle is None:
        return None
    try:
        kind, make_key = _identity_after(vehicle, new_vin=new_vin, restore_chassis_make_key=None)
    except UnprocessableEntityError:
        return None
    other = _record_carrying(db, vin=new_vin, kind=kind, make_key=make_key)
    return other if other is not None and other.id != vehicle.id else None


def _identity_after(
    vehicle: "VehicleMdm", *, new_vin: str, restore_chassis_make_key: str | None
) -> tuple["VehicleIdentifierKind", str | None]:
    """The kind and make key the record has once it carries `new_vin`
    (KAN-535): a valid VIN makes a VIN record; a chassis number keeps a
    chassis-number record's make key — or, in the compensation of a
    conversion, takes back the one it had. A VIN record is otherwise never
    given a chassis number: 422 `invalid_vin`, as before KAN-535."""

    from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind

    if is_valid_vin(new_vin):
        return VehicleIdentifierKind.VIN, None
    make_key = vehicle.chassis_make_key or restore_chassis_make_key
    if make_key is None or not is_chassis_number(new_vin):
        raise UnprocessableEntityError(
            f"'{new_vin}' is not a VIN, and vehicle {vehicle.vehicle_number} is identified by a VIN.",
            details={"field": "newVin", "reason": "invalid_vin"},
        )
    if vehicle.first_registration_date is None or vehicle.first_registration_date >= PRE_ISO_VIN_CUTOFF:
        # Reachable only restoring a record whose first registration moved
        # past 1980 meanwhile; ck_vehicle_mdm_chassis_number_identity would
        # refuse the write anyway.
        raise UnprocessableEntityError(
            "A chassis number that is not a 17-character VIN is kept only for a car first registered before 1981.",
            details={"field": "newVin", "reason": "chassis_number_requires_pre_1981_registration"},
        )
    return VehicleIdentifierKind.CHASSIS_NUMBER, make_key


def _record_carrying(
    db: Session, *, vin: str, kind: "VehicleIdentifierKind", make_key: str | None
) -> "VehicleMdm | None":
    """The record a value names: a VIN record by its VIN, a chassis-number
    record by its number and make key (KAN-535)."""

    from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind
    from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_by_chassis_number, get_vehicle_mdm_by_vin

    if kind == VehicleIdentifierKind.VIN or make_key is None:
        return get_vehicle_mdm_by_vin(db, vin)
    return get_vehicle_mdm_by_chassis_number(db, chassis_number=vin, make_key=make_key)


def _correct_record(
    own: Session,
    *,
    vehicle: "VehicleMdm",
    old_vin: str,
    new_vin: str,
    reason: str,
    actor_id: uuid.UUID | None,
    action: str,
    compensation: bool,
    restore_chassis_make_key: str | None = None,
) -> bool:
    """The record half, on Vehicle's own session; True when it wrote."""

    from app.vehicle.services.vehicle_mdm import publish_summary_changed_if_changed, summary_fields

    if vehicle.merged_into_vehicle_id is not None:
        raise ConflictError(
            f"Vehicle {vehicle.vehicle_number} was merged into another record; correct that one.",
            details={"reason": "vehicle_merged", "vehicleId": str(vehicle.id)},
        )
    if vehicle.vin == new_vin:
        # Already corrected — the retry of a correction whose caller's own
        # write failed after this committed. Nothing is written again.
        return False
    if vehicle.vin != old_vin:
        raise ConflictError(
            f"Vehicle {vehicle.vehicle_number} no longer carries VIN {old_vin}.",
            details={"reason": "vehicle_vin_changed", "vehicleId": str(vehicle.id)},
        )
    kind, make_key = _identity_after(vehicle, new_vin=new_vin, restore_chassis_make_key=restore_chassis_make_key)
    other = _record_carrying(own, vin=new_vin, kind=kind, make_key=make_key)
    if other is not None and other.id != vehicle.id:
        raise _belongs_to_other_vehicle(new_vin, other.vehicle_number)

    summary_before = summary_fields(vehicle)
    before: dict[str, object] = {"vin": old_vin}
    after: dict[str, object] = {"vin": new_vin}
    if kind != vehicle.identifier_kind:
        # KAN-535: the record changes kind (a chassis number corrected to a
        # VIN, or that undone); the audit row keeps the identity it had.
        before |= {"identifierKind": vehicle.identifier_kind.value, "chassisMakeKey": vehicle.chassis_make_key}
        after |= {"identifierKind": kind.value, "chassisMakeKey": make_key}
    vehicle.vin = new_vin
    vehicle.identifier_kind = kind
    vehicle.chassis_make_key = make_key
    vehicle.updated_by = actor_id
    vehicle.version += 1
    try:
        own.flush()
    except IntegrityError as exc:
        # A racing writer took the VIN between the look-up and the write.
        own.rollback()
        raced = _record_carrying(own, vin=new_vin, kind=kind, make_key=make_key)
        if raced is None:
            raise
        raise _belongs_to_other_vehicle(new_vin, raced.vehicle_number) from exc

    record_audit_event(
        own,
        entity_type="vehicle_mdm",
        entity_id=vehicle.id,
        # vehicle-mdm is global (ADR-022), as `update_vehicle_mdm`'s rows.
        tenant_id=None,
        action=action,
        actor_id=actor_id,
        before=before,
        after=after,
        reason=reason,
    )
    publish(
        own,
        OutboxEvent(
            event_type=VIN_CORRECTED_EVENT,
            tenant_id=None,
            producer="vehicle",
            aggregate_type="vehicle_mdm",
            aggregate_id=vehicle.id,
            payload={
                "vehicleId": str(vehicle.id),
                "version": vehicle.version,
                "previousVin": old_vin,
                "vin": new_vin,
                "correctedBy": str(actor_id) if actor_id else None,
                "compensation": compensation,
            },
        ),
    )
    publish_summary_changed_if_changed(own, vehicle=vehicle, before=summary_before)
    return True


def _belongs_to_other_vehicle(vin: str, vehicle_number: str) -> ConflictError:
    return ConflictError(
        f"VIN {vin} already belongs to vehicle {vehicle_number}; the two records are one car: merge them with «Merge into…» on the Vehicle 360.",
        details={"reason": "vin_belongs_to_other_vehicle", "vehicleNumber": vehicle_number},
    )
