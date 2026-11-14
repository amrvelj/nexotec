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

if TYPE_CHECKING:
    from app.vehicle.models.vehicle_mdm import VehicleMdm

VIN_CORRECTED_EVENT = "vehicle.vehicle_mdm.vin_corrected"


@dataclasses.dataclass(frozen=True)
class CorrectedVehicleVin:
    """What the correction changed — plain data, never the row.
    `record_changed` is False when the record already carried `new_vin`;
    `configuration_vin_changed` says this call wrote the configuration's
    VIN, `configuration_relinked` that it only re-derived its link (it
    already carried `new_vin`). The compensation reverts what these say —
    a re-linked configuration is re-linked, never given the old VIN back."""

    vehicle_id: uuid.UUID
    vehicle_number: str
    vin: str
    previous_vin: str
    record_changed: bool
    configuration_vin_changed: bool
    configuration_relinked: bool

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
) -> CorrectedVehicleVin:
    """Rewrites the vehicle-mdm record's VIN from `old_vin` to `new_vin`
    (both valid and normalised) and, when `configuration_id` is given, has
    that configuration of `tenant_id` follow — committed before this
    returns, on Vehicle's own session.

    `compensation=True` marks the call as the undo of an earlier one (the
    VINs swapped): its audit rows read `vin_correction_reverted`. With
    `include_record=False` (the compensation of a call that changed only the
    configuration) the record is left exactly as it is; with
    `relink_configuration_only=True` (the compensation of a configuration
    that was only re-linked) the configuration keeps its VIN and only its link
    is derived again.

    Refused, with nothing written: the record is unknown (404) or merged
    into another (409 `vehicle_merged`); it no longer carries `old_vin`
    (409 `vehicle_vin_changed`); `new_vin` already names another record
    (409 `vin_belongs_to_other_vehicle` with its `vehicleNumber`: the two
    records are one car and are merged by platform staff, FR-V-12); the
    configuration is not the tenant's (404) or names a third VIN (422
    `configuration_vin_differs`)."""

    from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
    from app.vehicle.services.configuration import follow_vehicle_vin_correction

    action = "vin_correction_reverted" if compensation else "vin_corrected"
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        vehicle = own.scalar(select(VehicleMdm).where(VehicleMdm.id == vehicle_id).with_for_update(key_share=True))
        if vehicle is None:
            raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
        if vehicle.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER:
            # KAN-535: a pre-1981 car's chassis-number record is that number
            # with its make; «VIN korrigieren» takes an ISO VIN and would make
            # it another kind of record. Refused before anything is written.
            raise UnprocessableEntityError(
                f"Vehicle {vehicle.vehicle_number} is identified by its chassis number, which is not corrected here.",
                details={"field": "newVin", "reason": "chassis_number_identity_fixed", "vehicleId": str(vehicle.id)},
            )
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
        own.commit()
        return CorrectedVehicleVin(
            vehicle_id=vehicle.id,
            vehicle_number=vehicle.vehicle_number,
            vin=vehicle.vin,
            previous_vin=old_vin,
            record_changed=record_changed,
            configuration_vin_changed=configuration_vin_changed,
            configuration_relinked=configuration_relinked,
        )


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
) -> bool:
    """The record half, on Vehicle's own session; True when it wrote."""

    from app.vehicle.services.vehicle_mdm import (
        get_vehicle_mdm_by_vin,
        publish_summary_changed_if_changed,
        summary_fields,
    )

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
    other = get_vehicle_mdm_by_vin(own, new_vin)
    if other is not None and other.id != vehicle.id:
        raise _belongs_to_other_vehicle(new_vin, other.vehicle_number)

    summary_before = summary_fields(vehicle)
    vehicle.vin = new_vin
    vehicle.updated_by = actor_id
    vehicle.version += 1
    try:
        own.flush()
    except IntegrityError as exc:
        # A racing writer took the VIN between the look-up and the write.
        own.rollback()
        raced = get_vehicle_mdm_by_vin(own, new_vin)
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
        before={"vin": old_vin},
        after={"vin": new_vin},
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
        f"VIN {vin} already belongs to vehicle {vehicle_number}; the two records are one car and are merged by platform staff.",
        details={"reason": "vin_belongs_to_other_vehicle", "vehicleNumber": vehicle_number},
    )
