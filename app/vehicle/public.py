"""The only surface other contexts may import from vehicle. Import-linter's
contract allows `app.<other-context>` to import `app.vehicle.public`, never
`app.vehicle.models` / `app.vehicle.services` / `app.vehicle.api` directly.
"""

import dataclasses
import datetime as dt
import logging
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy.orm import Session

from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.spec_block import (
    SPEC_BLOCK_ALL_FIELDS,
    spec_block_as_dict,
    spec_block_field_names,
)
from app.vehicle.models.vehicle import CustodyEventType, Vehicle, VehicleStatus
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.host_configuration import BUILD as CONFIGURATION_MODE_BUILD
from app.vehicle.services.host_configuration import RECORD as CONFIGURATION_MODE_RECORD
from app.vehicle.services.host_configuration import (
    ConfigurationLabel,
    ConfigurationModeNotAllowedError,
    ConfigurationPriceLine,
    HostConfiguration,
    NoCatalogueVariantError,
    ProviderValuationInput,
)
from app.vehicle.services.plate import PlateRecordingCheck, PlateVersionRef
from app.vehicle.services.plate_lookup_cache import purge_expired_plate_lookups
from app.vehicle.services.vehicle import create_custody_event, get_vehicle_or_404
from app.vehicle.services.vin_resolution import ResolvedVehicleMdm
from app.vehicle.services.vin_resolution import resolve_vehicle_mdm_by_vin as _resolve_vehicle_mdm_by_vin

logger = logging.getLogger(__name__)


# WP-7 PR-1: inventory's StockItem.vehicle_id references VehicleMdm (WP-5's
# three-layer model), not the legacy Vehicle above — the first consumer of
# this half of the surface.
#
# get_vehicle_mdm_or_404 is wrapped, not re-exported directly from
# app.vehicle.services.vehicle_mdm: that module imports app.vehicle.schemas.
# vehicle_mdm (for update_vehicle_mdm's parameter type), which imports
# app.customer.public — and app.customer's services import THIS module, so a
# module-level import here would be a real import cycle, not just a slow
# one. Deferred to call time, well after both modules have finished
# loading at process startup.
def get_vehicle_mdm_or_404(db: Session, vehicle_id: uuid.UUID) -> VehicleMdm:
    from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404 as _get_vehicle_mdm_or_404

    return _get_vehicle_mdm_or_404(db, vehicle_id)


@dataclasses.dataclass(frozen=True)
class VehicleSummary:
    """The display fields another context denormalises about a vehicle_mdm
    row (CLAUDE.md rule 2's label, KAN-84) — plain data, never the ORM row.
    make/model/trim come from the OPTIONAL catalogue match and model_year
    from first registration, so all four may be None."""

    id: uuid.UUID
    vin: str
    vehicle_number: str
    make: str | None
    model: str | None
    model_year: int | None
    trim: str | None


_SUMMARY_BATCH = 500


def get_vehicle_summaries(db: Session, vehicle_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, VehicleSummary]:
    """KAN-84 — the label source for customer's VehicleParty (and any other
    context that keeps a vehicle label), replacing the cross-context ORM
    relationship it used to join through. One query per 500 ids, with the
    catalogue chain (`catalogue_variant -> model_group -> brand`) eager-
    loaded so make/model/trim never lazy-load per row. An id with no
    vehicle_mdm row is simply absent from the result — dangling references
    are the nightly reconciliation's to report, not this read's to raise on.
    """

    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.vehicle.models.catalogue import ModelGroup, ModelVariant

    ids = list(dict.fromkeys(vehicle_ids))
    summaries: dict[uuid.UUID, VehicleSummary] = {}
    for start in range(0, len(ids), _SUMMARY_BATCH):
        rows = db.scalars(
            select(VehicleMdm)
            .options(
                joinedload(VehicleMdm.catalogue_variant).options(
                    joinedload(ModelVariant.model_group).options(joinedload(ModelGroup.brand))
                )
            )
            .where(VehicleMdm.id.in_(ids[start : start + _SUMMARY_BATCH]))
        ).all()
        for v in rows:
            summaries[v.id] = VehicleSummary(
                id=v.id,
                vin=v.vin,
                vehicle_number=v.vehicle_number,
                make=v.make,
                model=v.model,
                model_year=v.model_year,
                trim=v.trim,
            )
    return summaries


def create_or_get_vehicle_mdm(
    db: Session, *, vin: str, catalogue_variant_id: uuid.UUID | None = None, actor_id: uuid.UUID | None = None
) -> tuple[VehicleMdm, bool]:
    """Found or created on the session it is handed, committing it — for
    Vehicle's own callers and test set-up only. Another context resolves a
    VIN through `resolve_vehicle_mdm_by_vin` below, which runs this on a
    session of its own (KAN-337; `test_vin_resolution_is_vehicles.py`
    refuses a call from any other context). Same deferred-import reasoning
    as get_vehicle_mdm_or_404 above.
    """

    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm as _create_or_get_vehicle_mdm

    return _create_or_get_vehicle_mdm(db, vin=vin, catalogue_variant_id=catalogue_variant_id, actor_id=actor_id)


def resolve_vehicle_mdm_by_vin(
    db: Session, *, vin: str, actor_id: uuid.UUID | None, catalogue_variant_id: uuid.UUID | None = None
) -> ResolvedVehicleMdm:
    """FR-V-04/FR-V-15: the vehicle-mdm record for `vin`, created if
    vehicle-mdm has never seen it — committed before this returns, on a
    session of Vehicle's own bound to `db`'s database, so the caller's
    pending work is neither committed nor rolled back, and a lost insert
    race resolves to the winner's record (KAN-257). Stock's promotion at
    purchase booking (ADR-045, ADR-103) is the caller today; KAN-337 moved
    the resolution here from `inventory/services/vehicle_resolution.py` so
    no context keeps a copy. Returns plain data. `actor_id` is the user
    whose request created the record — required, so no caller leaves out who
    acted; `None` only for a system write (KAN-243).
    """

    return _resolve_vehicle_mdm_by_vin(db, vin=vin, catalogue_variant_id=catalogue_variant_id, actor_id=actor_id)


def get_vehicle_equipment(db: Session, vehicle_id: uuid.UUID) -> dict:
    """WP-7 PR-8 (ADR-062) — same deferred-import reasoning as the two
    functions above."""

    from app.vehicle.services.vehicle_mdm import get_vehicle_equipment as _get_vehicle_equipment

    return _get_vehicle_equipment(db, vehicle_id)


def has_current_energy_rating(db: Session, vehicle_id: uuid.UUID) -> bool:
    """WP-7 PR-8's Energieetikette blocking-condition check."""

    from app.vehicle.services.vehicle_mdm import has_current_energy_rating as _has_current_energy_rating

    return _has_current_energy_rating(db, vehicle_id)


def match_vehicle(
    db: Session,
    *,
    vin: str | None = None,
    stammnummer: str | None = None,
    plate: str | None = None,
    canton: str | None = None,
    vehicle_kind: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date=None,
    group_id: uuid.UUID,
):
    """WP-8 PR-5 — FR-S-08's trade-in plate/VIN search, the first consumer
    outside vehicle itself. `app.vehicle.services.matching` has no import
    of app.customer at all, so this one doesn't strictly need the deferred
    idiom the three above do — kept as a plain top-level import for that
    reason, re-exporting the exact same `MatchResult`.

    `group_id` is required (KAN-467): a plate is matched in the caller's dealer
    group's register only — the caller passes the token's group.
    """

    from app.vehicle.services.matching import match_vehicle as _match_vehicle

    return _match_vehicle(
        db,
        vin=vin,
        stammnummer=stammnummer,
        plate=plate,
        canton=canton,
        vehicle_kind=vehicle_kind,
        type_approval_number=type_approval_number,
        first_registration_date=first_registration_date,
        group_id=group_id,
    )


def check_plate_handover(
    db: Session,
    *,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    plate: str,
    canton: str,
    valid_from: dt.date,
    move_from: Sequence[PlateVersionRef] = (),
) -> PlateRecordingCheck:
    """KAN-523 — Sales' «Rechnung anfordern» asks whether the plate the sold
    car leaves with can be recorded on it in the group's register, before it
    publishes `sales.contract.invoice_requested` (whose vehicle consumer
    records it). Raises the register's refusals — 409
    `plate_current_on_other_vehicle` names the group's other car(s) through
    the plate-read guard, which commits its audit rows: hand it a session of
    your own (ADR-047)."""

    from app.vehicle.services.plate_handover import check_plate_handover as _check_plate_handover

    return _check_plate_handover(
        db,
        actor_id=actor_id,
        tenant_id=tenant_id,
        group_id=group_id,
        vehicle_id=vehicle_id,
        plate=plate,
        canton=canton,
        valid_from=valid_from,
        move_from=move_from,
    )


def get_configuration_for_host(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID
) -> HostConfiguration:
    """C-F (KAN-10) — what an offer, a stock item or a valuation reads from
    the configuration it stores. 404 for another tenant's configuration.
    Deferred import: `configuration_host` reaches `services.vehicle_mdm`,
    the cycle described above `get_vehicle_mdm_or_404`."""

    from app.vehicle.services.configuration_host import get_configuration_for_host as _get

    return _get(db, tenant_id=tenant_id, configuration_id=configuration_id)


def get_provider_valuation_input(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID
) -> ProviderValuationInput | None:
    """KAN-552 — what the identAPI's valuation needs about a configured car:
    the Betrieb's vehicle-data connection, the FzKey at that provider, the
    variant's model-year range, the first registration, the mileage and the
    selected options' OptKeys. `None` without a connection; 404 for another
    Betrieb's configuration; 422 `no_catalogue_variant` (`NoCatalogueVariantError`)
    when the car has no FzKey there. A read; deferred import as above."""

    from app.vehicle.services.provider_valuation_input import provider_valuation_input as _read

    return _read(db, tenant_id=tenant_id, configuration_id=configuration_id)


def get_configuration_labels(
    db: Session, tenant_by_configuration: dict[uuid.UUID, uuid.UUID]
) -> dict[uuid.UUID, ConfigurationLabel]:
    """KAN-534 — each configuration's current label, for Stock's nightly
    refresh of `stock_item.configuration_label` (rule 2), read only in the
    Betrieb each host row names (configuration id -> tenant id). A read;
    deferred import as above."""

    from app.vehicle.services.configuration_host import get_configuration_labels as _labels

    return _labels(db, tenant_by_configuration)


def copy_configuration_for_host(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID, actor_id: uuid.UUID | None
) -> HostConfiguration:
    """FR-C-11 (KAN-188) — a host that copies itself (Sales' Save-and-copy)
    gets its own copy of the configuration it stores, never a second
    reference to the same one. Commits its own transaction (ADR-047): the
    caller hands it a session of its own and, if its own write then fails,
    calls `discard_configuration_copy`. Deferred import as above."""

    from app.vehicle.services.configuration_host import copy_configuration_for_host as _copy

    return _copy(db, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=actor_id)


def discard_configuration_copy(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID, actor_id: uuid.UUID | None
) -> None:
    """KAN-188 — the compensating action of `copy_configuration_for_host`,
    for a copy no host stored. Commits its own transaction (ADR-047)."""

    from app.vehicle.services.configuration_host import discard_host_configuration_copy as _discard

    _discard(db, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=actor_id)


def run_daily_plate_lookup_purge(db: Session) -> None:
    """KAN-42 (C-D) — the daily job that deletes plate-lookup cache rows past
    their TTL (`app.vehicle.services.plate_lookup_cache`). Registered in
    `app/worker.py`."""

    deleted = purge_expired_plate_lookups(db)
    if deleted:
        logger.info("plate-lookup cache purge", extra={"deleted": deleted})


__all__ = [
    "CONFIGURATION_MODE_BUILD",
    "CONFIGURATION_MODE_RECORD",
    "SPEC_BLOCK_ALL_FIELDS",
    "ConfigurationLabel",
    "ConfigurationModeNotAllowedError",
    "ConfigurationPriceLine",
    "CustodyEventType",
    "HostConfiguration",
    "NoCatalogueVariantError",
    "PlateRecordingCheck",
    "PlateVersionRef",
    "ProviderValuationInput",
    "ResolvedVehicleMdm",
    "Vehicle",
    "VehicleConfiguration",
    "VehicleMdm",
    "VehicleStatus",
    "VehicleSummary",
    "check_plate_handover",
    "copy_configuration_for_host",
    "create_custody_event",
    "create_or_get_vehicle_mdm",
    "discard_configuration_copy",
    "get_configuration_for_host",
    "get_configuration_labels",
    "get_provider_valuation_input",
    "get_vehicle_equipment",
    "get_vehicle_mdm_or_404",
    "get_vehicle_or_404",
    "get_vehicle_summaries",
    "has_current_energy_rating",
    "match_vehicle",
    "resolve_vehicle_mdm_by_vin",
    "run_daily_plate_lookup_purge",
    "spec_block_as_dict",
    "spec_block_field_names",
]
