"""The vehicle list's ONE search box (WP-5 PR-9, FR-V-06/FR-V-16). The
string's own shape decides whether it resolves as an identifier or
filters the grid — never a second field, never a mode the user picks.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams, build_page, paginate_query
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.vehicle_mdm import VehiclePickerCandidate
from app.vehicle.services.plate import resolve_plate

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_VEHICLE_NUMBER_RE = re.compile(r"^F-\d{6}$", re.IGNORECASE)
_STAMMNUMMER_RE = re.compile(r"^\d{9}$")
# Swiss plate shape: 1-2 letters (canton) + space + digits. Deliberately
# loose — a false-positive here just means a plate-shaped filter string
# also gets tried as an identifier and comes back empty, falling through
# to the filter path; a false negative would wrongly treat a real plate
# as a filter, which is the worse failure.
_PLATE_RE = re.compile(r"^([A-Z]{1,2})[\s-]?(\d{1,6})$", re.IGNORECASE)


@dataclass(frozen=True)
class SearchResolution:
    resolved: VehicleMdm | None
    picker_candidates: list[VehiclePickerCandidate]


def resolve_identifier(db: Session, query: str) -> SearchResolution | None:
    """Returns None if `query` doesn't look like any identifier at all —
    the caller then falls through to an ordinary grid filter. Returns a
    SearchResolution (possibly with no hit at all, i.e. resolved=None and
    an empty picker) if it DOES look like one, since "looks like a VIN but
    matches nothing" is still a resolve attempt, not a filter.
    """

    q = query.strip()

    if _VIN_RE.match(q):
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.vin == q))
        return SearchResolution(resolved=vehicle, picker_candidates=[])

    if _VEHICLE_NUMBER_RE.match(q):
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.vehicle_number == q.upper()))
        return SearchResolution(resolved=vehicle, picker_candidates=[])

    if _STAMMNUMMER_RE.match(q):
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.stammnummer == q))
        return SearchResolution(resolved=vehicle, picker_candidates=[])

    plate_match = _PLATE_RE.match(q)
    if plate_match:
        canton, number = plate_match.group(1).upper(), plate_match.group(2)
        plate_str = f"{canton} {number}"
        rows = resolve_plate(db, plate=plate_str, canton=canton)
        if len(rows) == 1:
            vehicle = db.get(VehicleMdm, rows[0].vehicle_id)
            return SearchResolution(resolved=vehicle, picker_candidates=[])
        if len(rows) > 1:
            # Distinguish a Wechselschild pair (shared plate_group_id, never
            # a conflict) from a genuine conflict (ADR-039) — same rule
            # PR-4's record_plate_assignment uses to decide whether to
            # raise a VehiclePlateConflict in the first place.
            group_ids = {r.plate_group_id for r in rows if r.plate_group_id is not None}
            is_wechselschild = len(group_ids) == 1 and len(rows) == 2
            candidates = [
                VehiclePickerCandidate(
                    id=r.vehicle_id, vehicle_number=_vehicle_number(db, r.vehicle_id), vin=_vin(db, r.vehicle_id),
                    plate=r.plate, plate_group_id=r.plate_group_id, is_conflict=not is_wechselschild,
                )
                for r in rows
            ]
            return SearchResolution(resolved=None, picker_candidates=candidates)
        return SearchResolution(resolved=None, picker_candidates=[])

    return None


def _vehicle_number(db: Session, vehicle_id: uuid.UUID) -> str:
    vehicle = db.get(VehicleMdm, vehicle_id)
    return vehicle.vehicle_number if vehicle else ""


def _vin(db: Session, vehicle_id: uuid.UUID) -> str:
    vehicle = db.get(VehicleMdm, vehicle_id)
    return vehicle.vin if vehicle else ""


def filter_vehicles(db: Session, *, query: str | None, params: PageParams) -> tuple[list[VehicleMdm], str | None]:
    """The ordinary grid filter path — anything that didn't resolve as an
    identifier. Filters on the vehicle's own fields only; filtering by
    brand/model/variant name is FR-V-01's catalogue search, a distinct
    surface from this list per the PRD's own module split, not folded in
    here.
    """

    stmt = select(VehicleMdm).where(VehicleMdm.merged_into_vehicle_id.is_(None))
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(VehicleMdm.vin.ilike(like), VehicleMdm.vehicle_number.ilike(like)))
    stmt = paginate_query(stmt, model=VehicleMdm, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)
