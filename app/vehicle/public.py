"""The only surface other contexts may import from vehicle. Import-linter's
contract allows `app.<other-context>` to import `app.vehicle.public`, never
`app.vehicle.models` / `app.vehicle.services` / `app.vehicle.api` directly.
"""

import uuid

from sqlalchemy.orm import Session

from app.vehicle.models.vehicle import CustodyEventType, Vehicle, VehicleStatus
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.catalogue_sync import (
    NoVehicleDataConnectionError,
    SyncResult,
    check_sync_age_alarm_for_tenant,
    run_daily_delta_for_tenant,
    seed_tenant_catalogue,
)
from app.vehicle.services.vehicle import create_custody_event, get_vehicle_or_404


# WP-7 PR-1: inventory's StockItem.vehicle_id references VehicleMdm (WP-5's
# three-layer model), not the legacy Vehicle above — the first consumer of
# this half of the surface.
#
# get_vehicle_mdm_or_404 is wrapped, not re-exported directly from
# app.vehicle.services.vehicle_mdm: that module imports app.vehicle.schemas.
# vehicle_mdm (for update_vehicle_mdm's parameter type), which imports
# app.customer.public — and app.customer.models.vehicle_party imports THIS
# module (for the legacy Vehicle/VehicleStatus types above), so a
# module-level import here would be a real import cycle, not just a slow
# one. Deferred to call time, well after both modules have finished
# loading at process startup.
def get_vehicle_mdm_or_404(db: Session, vehicle_id: uuid.UUID) -> VehicleMdm:
    from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404 as _get_vehicle_mdm_or_404

    return _get_vehicle_mdm_or_404(db, vehicle_id)


def create_or_get_vehicle_mdm(
    db: Session, *, vin: str, catalogue_variant_id: uuid.UUID | None = None
) -> tuple[VehicleMdm, bool]:
    """WP-7 PR-2: inventory's promote_to_vehicle_mdm (ADR-045, FR-V-04)
    calls this the moment a pipeline item's VIN arrives — same
    deferred-import reasoning as get_vehicle_mdm_or_404 above.
    """

    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm as _create_or_get_vehicle_mdm

    return _create_or_get_vehicle_mdm(db, vin=vin, catalogue_variant_id=catalogue_variant_id)


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
):
    """WP-8 PR-5 — FR-S-08's trade-in plate/VIN search, the first consumer
    outside vehicle itself. `app.vehicle.services.matching` has no import
    of app.customer at all, so this one doesn't strictly need the deferred
    idiom the three above do — kept as a plain top-level import for that
    reason, re-exporting the exact same `MatchResult`.
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
    )


__all__ = [
    "CustodyEventType",
    "NoVehicleDataConnectionError",
    "SyncResult",
    "Vehicle",
    "VehicleMdm",
    "VehicleStatus",
    "check_sync_age_alarm_for_tenant",
    "create_custody_event",
    "create_or_get_vehicle_mdm",
    "get_vehicle_equipment",
    "get_vehicle_mdm_or_404",
    "get_vehicle_or_404",
    "has_current_energy_rating",
    "match_vehicle",
    "run_daily_delta_for_tenant",
    "seed_tenant_catalogue",
]
