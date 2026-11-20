"""The one-off repair of configuration VINs stored before KAN-338 (KAN-395,
KAN-748) — `scripts/repair_configuration_vins.py` runs it. Split out of
`configuration.py` along its seam (the file-size ratchet, KAN-617): it shares
only the configuration's entity type, its event and its VIN link rule."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.validators import (
    PRE_ISO_VIN_CUTOFF,
    is_chassis_number,
    is_chassis_number_spelling,
    is_valid_vin,
    normalise_vin,
)
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.services.configuration import _ENTITY_TYPE, _link_vehicle_if_vin_resolves, _publish

VIN_REPAIR_REASON = "KAN-395: a VIN stored before KAN-338 that no save could pass"


@dataclass(frozen=True)
class VinRepair:
    configuration_id: uuid.UUID
    tenant_id: uuid.UUID
    before: str
    after: str | None  # None: cleared
    # KAN-748: a pre-1981 chassis number with no one spelling to give it — left
    # as stored (`after == before`), nothing written, listed for a hand correction.
    listed: bool = False


def repair_stored_vins(db: Session) -> list[VinRepair]:
    """KAN-395 (Anto, 2026-10-20, option B: clean the data once). Before
    KAN-338 a configuration's `vin` was stored as typed, with no
    normalisation and no ISO check, and since KAN-353 every PATCH judges the
    stored value: one that is no VIN counts as a chassis number and is
    refused unless the car was first registered before 1981 — so such a
    configuration could no longer be saved, and until KAN-340 gave the
    configure phase a VIN field the user could not correct it on screen.

    - **A car first registered from 1981 on, or with none:** every
      configuration whose stored `vin` `require_vin_or_pre_1981_chassis_number`
      would refuse — not a valid VIN.
    - **What it writes there:** the VIN normalised as every typed VIN is (upper
      case, no whitespace) when that makes it a valid VIN; otherwise the VIN
      is cleared — it names no car, and cannot be kept. The vehicle link is
      re-derived from the new value, as on every VIN write (KAN-326).
    - **A car first registered before 1981** (KAN-748; Anto, 2026-11-18,
      option A): its chassis number has a VIN's one spelling (KAN-391,
      option A). One stored before KAN-338 in another spelling is normalised
      when that gives the one spelling («911 230 0123» → «9112300123»);
      one that does not («911 230-0123»: punctuation is never removed, since
      which character it hides is a person's call) is left as stored and
      returned with `listed`, so the dry run names it for a hand correction.
      It is never cleared: the number still names the car.
    - Each changed configuration's `version` moves once, an audit record with
      no actor (the system) and `VIN_REPAIR_REASON` keeps the old value —
      `updated_by` is cleared to match — and `configuration.updated` is
      published, as `fill_null_spec_fields_from_variants` does. Nothing is
      committed here (the caller's transaction, which a dry run rolls back);
      vehicle-mdm is never written (ADR-070). A valuation is never edited
      (valuation.md), so its stored VIN is not this function's.
    """

    configurations = db.scalars(
        select(VehicleConfiguration)
        .where(VehicleConfiguration.vin.is_not(None))
        .order_by(VehicleConfiguration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()

    repairs: list[VinRepair] = []
    for configuration in configurations:
        before = configuration.vin
        if before is None or not is_chassis_number(before):
            continue  # empty, or a valid VIN: every save passes it
        registered = configuration.first_registration_date
        normalised = normalise_vin(before)
        if registered is not None and registered < PRE_ISO_VIN_CUTOFF:
            if is_chassis_number_spelling(before):
                continue  # already the one spelling
            if not is_chassis_number_spelling(normalised):
                repairs.append(
                    VinRepair(
                        configuration_id=configuration.id,
                        tenant_id=configuration.tenant_id,
                        before=before,
                        after=before,
                        listed=True,
                    )
                )
                continue
            after: str | None = normalised
        else:
            after = normalised if is_valid_vin(normalised) else None
        vehicle_before = configuration.vehicle_id
        configuration.vin = after
        _link_vehicle_if_vin_resolves(db, configuration)
        configuration.version += 1
        configuration.updated_by = None  # a system write (KAN-243: null actor), like its audit record
        db.flush()
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=configuration.id,
            tenant_id=configuration.tenant_id,
            action="vin_repair",
            actor_id=None,
            before={"vin": before, "vehicleId": str(vehicle_before) if vehicle_before else None},
            after={
                "vin": after,
                "vehicleId": str(configuration.vehicle_id) if configuration.vehicle_id else None,
            },
            reason=VIN_REPAIR_REASON,
        )
        _publish(db, configuration, "configuration.updated")
        repairs.append(
            VinRepair(configuration_id=configuration.id, tenant_id=configuration.tenant_id, before=before, after=after)
        )
    return repairs
