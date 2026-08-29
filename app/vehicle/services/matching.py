"""The matching waterfall (WP-5 PR-6, FR-V-01/FR-V-04/FR-V-12 all call
into this), used by import, manual creation and the aftersales counter, in
one fixed order:

1. vin exact — decisive
2. stammnummer exact — decisive for Swiss vehicles
3. plate valid today + vehicle_kind — probable, needs confirmation
4. type approval + first registration + new price — MatchCode 1 or 2,
   confirmation always required in this package (the real FahrzeugeMatch
   algorithm is WP-6's provider-gateway; this rung is a local-catalogue
   heuristic standing in for it until then, so it never claims the
   certainty a real provider match would)
5. no match — create new
"""

import dataclasses
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.plate import resolve_plate


@dataclasses.dataclass(frozen=True)
class MatchResult:
    rung: str  # "vin" | "stammnummer" | "plate" | "type_approval" | "no_match"
    vehicle: VehicleMdm | None
    decisive: bool
    requires_confirmation: bool
    match_code: int | None = None  # only set on the type_approval rung


_NO_MATCH = MatchResult(rung="no_match", vehicle=None, decisive=False, requires_confirmation=False)


def match_vehicle(
    db: Session,
    *,
    vin: str | None = None,
    stammnummer: str | None = None,
    plate: str | None = None,
    canton: str | None = None,
    vehicle_kind: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date: dt.date | None = None,
) -> MatchResult:
    if vin:
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.vin == vin))
        if vehicle is not None:
            return MatchResult(rung="vin", vehicle=vehicle, decisive=True, requires_confirmation=False)

    if stammnummer:
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.stammnummer == stammnummer))
        if vehicle is not None:
            return MatchResult(rung="stammnummer", vehicle=vehicle, decisive=True, requires_confirmation=False)

    if plate and canton:
        plate_rows = resolve_plate(db, plate=plate, canton=canton)
        candidate_ids = {row.vehicle_id for row in plate_rows}
        if len(candidate_ids) == 1:
            vehicle = db.get(VehicleMdm, next(iter(candidate_ids)))
            if vehicle is not None and (vehicle_kind is None or _vehicle_kind_of(vehicle) == vehicle_kind):
                return MatchResult(rung="plate", vehicle=vehicle, decisive=False, requires_confirmation=True)

    if type_approval_number:
        stmt = select(VehicleMdm).where(VehicleMdm.type_approval_number == type_approval_number)
        if first_registration_date is not None:
            stmt = stmt.where(VehicleMdm.first_registration_date == first_registration_date)
        candidates = list(db.scalars(stmt).all())
        if len(candidates) == 1:
            # "Unique" in our local catalogue only — not the real provider
            # match, so still always confirmed (see module docstring).
            return MatchResult(
                rung="type_approval", vehicle=candidates[0], decisive=False, requires_confirmation=True, match_code=1
            )
        if len(candidates) > 1:
            return MatchResult(
                rung="type_approval", vehicle=candidates[0], decisive=False, requires_confirmation=True, match_code=2
            )

    return _NO_MATCH


def _vehicle_kind_of(vehicle: VehicleMdm) -> str | None:
    if vehicle.catalogue_variant is None:
        return None
    return vehicle.catalogue_variant.vehicle_kind
