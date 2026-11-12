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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish

VIN_CORRECTED_EVENT = "vehicle.vehicle_mdm.vin_corrected"


@dataclasses.dataclass(frozen=True)
class CorrectedVehicleVin:
    """What the correction left behind — plain data, never the row.
    `changed` is False when the record already carried `new_vin`."""

    vehicle_id: uuid.UUID
    vehicle_number: str
    vin: str
    previous_vin: str
    changed: bool


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
) -> CorrectedVehicleVin:
    """Rewrites the vehicle-mdm record's VIN from `old_vin` to `new_vin`
    (both valid and normalised) and, when `configuration_id` is given, has
    that configuration of `tenant_id` follow — committed before this
    returns, on Vehicle's own session.

    Refused, with nothing written: the record is unknown (404) or merged
    into another (409 `vehicle_merged`); it no longer carries `old_vin`
    (409 `vehicle_vin_changed`); `new_vin` already names another record
    (409 `vin_belongs_to_other_vehicle` with its `vehicleNumber`: the two
    records are one car and are merged by platform staff, FR-V-12); the
    configuration is not the tenant's (404) or names a third VIN (422
    `configuration_vin_differs`)."""

    from app.vehicle.models.vehicle_mdm import VehicleMdm
    from app.vehicle.services.configuration import follow_vehicle_vin_correction
    from app.vehicle.services.vehicle_mdm import (
        get_vehicle_mdm_by_vin,
        publish_summary_changed_if_changed,
        summary_fields,
    )

    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        vehicle = own.scalar(select(VehicleMdm).where(VehicleMdm.id == vehicle_id).with_for_update(key_share=True))
        if vehicle is None:
            raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
        if vehicle.merged_into_vehicle_id is not None:
            raise ConflictError(
                f"Vehicle {vehicle.vehicle_number} was merged into another record; correct that one.",
                details={"reason": "vehicle_merged", "vehicleId": str(vehicle.id)},
            )
        if vehicle.vin == new_vin:
            # Already corrected — the retry of a correction whose caller's
            # own write failed after this committed. The configuration may
            # still need to follow (the same idempotent rule).
            if configuration_id is not None:
                follow_vehicle_vin_correction(
                    own,
                    tenant_id=tenant_id,
                    configuration_id=configuration_id,
                    old_vin=old_vin,
                    new_vin=new_vin,
                    reason=reason,
                    actor_id=actor_id,
                )
            own.commit()
            return CorrectedVehicleVin(
                vehicle_id=vehicle.id,
                vehicle_number=vehicle.vehicle_number,
                vin=vehicle.vin,
                previous_vin=old_vin,
                changed=False,
            )
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
            action="vin_correction_reverted" if compensation else "vin_corrected",
            actor_id=actor_id,
            before={"vin": old_vin},
            after={"vin": new_vin, "correctedFor": str(tenant_id)},
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
        if configuration_id is not None:
            follow_vehicle_vin_correction(
                own,
                tenant_id=tenant_id,
                configuration_id=configuration_id,
                old_vin=old_vin,
                new_vin=new_vin,
                reason=reason,
                actor_id=actor_id,
            )
        own.commit()
        return CorrectedVehicleVin(
            vehicle_id=vehicle.id,
            vehicle_number=vehicle.vehicle_number,
            vin=new_vin,
            previous_vin=old_vin,
            changed=True,
        )


def _belongs_to_other_vehicle(vin: str, vehicle_number: str) -> ConflictError:
    return ConflictError(
        f"VIN {vin} already belongs to vehicle {vehicle_number}; the two records are one car and are merged by platform staff.",
        details={"reason": "vin_belongs_to_other_vehicle", "vehicleNumber": vehicle_number},
    )
