"""Locations (KAN-204; Dealer Administration PRD › Locations; D-A-02, D-A-27,
FR-A-03, A-RULE-1): the Betrieb's sites as full master data, their opening
hours and closures.

Rules held here, not in the database:
- **A location is never a scoping boundary** (ADR-014): nothing here filters
  another record by it; the tenant is the dealership from the token.
- **Deactivate, never delete** (A-RULE-1): there is no delete; `is_active`
  goes false, the row stays referenceable by every document that names it.
- **FR-A-03**: a Betrieb always has at least one active location — the last
  one cannot be deactivated (409 `last_active_location`), judged under the
  dealership row lock so two concurrent deactivations cannot both pass. A new
  Betrieb gets its first location from its registered seat
  (`new_first_location`, called by `create_dealership`).
- **`code` is immutable once saved**: a location saved without one (the
  first location of a new Betrieb, the rows from before KAN-204) may be given
  one once; then never changed (422 `location_code_immutable`). Unique per
  Betrieb (409 `location_code_taken`).
- The address is judged as a block: street, number, postal code and locality
  together, the canton required for a Swiss address and absent for any other.
- `bankAccountOverrideId` names one of this Betrieb's bank accounts (422
  `unknown_bank_account` otherwise, never a 404 that would confirm another
  Betrieb's account).
- Opening hours are stored per department always; the one-set mode writes
  the same week for all three (D-A-27).

Every write is audit-logged with before and after values.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.constants import CANTON_CODES
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.validators import validate_postal_code_for_country
from app.platform.models.dealership import Dealership, Location
from app.platform.models.dealership_bank_account import DealershipBankAccount
from app.platform.models.location_schedule import LocationClosure, LocationDepartment, LocationOpeningHours
from app.platform.schemas.location import (
    ClosureCreate,
    ClosureUpdate,
    LocationCreate,
    LocationUpdate,
    OpeningHoursUpdate,
)
from app.platform.services import holidays as holiday_service
from app.platform.services.dealership import get_dealership_for_update_or_404

_ENTITY = "location"
_HOURS_ENTITY = "location_opening_hours"
_CLOSURE_ENTITY = "location_closure"

_ADDRESS_FIELDS = (
    "address_street",
    "address_house_number",
    "address_postal_code",
    "address_locality",
    "address_canton",
    "address_country",
)
_SNAPSHOT_FIELDS = (
    "code",
    "name",
    "location_types",
    *_ADDRESS_FIELDS,
    "contact_person",
    "phone",
    "email",
    "lead_email",
    "geo_lat",
    "geo_lng",
    "holiday_calendar_id",
    "letterhead_address_override",
    "bank_account_override_id",
    "is_active",
    "valid_from",
    "valid_to",
)
_GEO_QUANTUM = Decimal("0.000001")
_DEPARTMENTS = tuple(LocationDepartment)


def _plain(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (uuid.UUID, dt.date, dt.time)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _snapshot(location: Location) -> dict[str, Any]:
    return {field: _plain(getattr(location, field)) for field in _SNAPSHOT_FIELDS}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {k: v for k, v in before.items() if after.get(k) != v},
        {k: v for k, v in after.items() if before.get(k) != v},
    )


def _geo(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(_GEO_QUANTUM)


# --- Reads ------------------------------------------------------------------------


def list_locations(db: Session, *, dealership_id: uuid.UUID, active: bool | None = None) -> list[Location]:
    stmt = select(Location).where(Location.tenant_id == dealership_id)
    if active is not None:
        stmt = stmt.where(Location.is_active.is_(active))
    return list(db.scalars(stmt.order_by(Location.name, Location.id)).all())


def get_location_or_404(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID, for_update: bool = False
) -> Location:
    """By (dealership, id): another Betrieb's location is a 404 (rule 7)."""

    stmt = select(Location).where(Location.tenant_id == dealership_id, Location.id == location_id)
    if for_update:
        stmt = stmt.with_for_update()
    location = db.scalar(stmt)
    if location is None:
        raise NotFoundError(f"Location {location_id} was not found.")
    return location


# --- Validation of a merged row ------------------------------------------------------


def _assert_address_block(values: dict[str, Any]) -> None:
    core = [values[f] for f in ("address_street", "address_house_number", "address_postal_code", "address_locality")]
    if not any(core) and not values["address_canton"]:
        return  # no address (a row from before KAN-204 may hold none)
    if not all(core):
        raise UnprocessableEntityError(
            "An address needs street, house number, postal code and locality together.",
            details={"reason": "location_address_incomplete"},
        )
    country = (values["address_country"] or "CH").upper()
    values["address_country"] = country
    try:
        values["address_postal_code"] = validate_postal_code_for_country(values["address_postal_code"], country)
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc), details={"reason": "location_postal_code_invalid"}) from exc
    canton = values["address_canton"]
    if country == "CH":
        if canton not in CANTON_CODES:
            raise UnprocessableEntityError(
                "A Swiss address needs its canton.", details={"reason": "location_canton_required"}
            )
    elif canton is not None:
        raise UnprocessableEntityError(
            "The canton applies to Swiss addresses only — leave it out for another country.",
            details={"reason": "location_canton_not_swiss"},
        )


def _assert_consistent(db: Session, *, dealership_id: uuid.UUID, values: dict[str, Any]) -> None:
    _assert_address_block(values)
    if (values["geo_lat"] is None) != (values["geo_lng"] is None):
        raise UnprocessableEntityError(
            "geoLat and geoLng go together — send both or clear both.", details={"reason": "location_geo_incomplete"}
        )
    if values["valid_from"] is not None and values["valid_to"] is not None and values["valid_to"] < values["valid_from"]:
        raise UnprocessableEntityError(
            "validTo must not lie before validFrom.", details={"reason": "location_validity_inverted"}
        )
    if values["holiday_calendar_id"] is not None:
        holiday_service.get_active_calendar_or_422(db, values["holiday_calendar_id"])
    if values["bank_account_override_id"] is not None:
        account = db.scalar(
            select(DealershipBankAccount.id).where(
                DealershipBankAccount.id == values["bank_account_override_id"],
                DealershipBankAccount.dealership_id == dealership_id,
            )
        )
        if account is None:
            raise UnprocessableEntityError(
                "bankAccountOverrideId names no bank account of this business.",
                details={"reason": "unknown_bank_account"},
            )


def _code_conflict(code: str | None) -> ConflictError:
    return ConflictError(
        f"Another location of this business already has the code {code}.",
        details={"reason": "location_code_taken", "code": code},
    )


# --- Location writes -------------------------------------------------------------------


def create_location(
    db: Session, *, dealership_id: uuid.UUID, data: LocationCreate, actor_id: uuid.UUID
) -> Location:
    values = data.model_dump()
    values["location_types"] = [t.value for t in data.location_types]
    if "holiday_calendar_id" not in data.model_fields_set:
        default = holiday_service.default_calendar_for_canton(db, data.address_canton)
        values["holiday_calendar_id"] = default.id if default is not None else None
    if values["address_street"] is None:
        raise UnprocessableEntityError(
            "A new location needs its address.", details={"reason": "location_address_incomplete"}
        )
    _assert_consistent(db, dealership_id=dealership_id, values=values)
    location = Location(
        tenant_id=dealership_id,
        **{k: v for k, v in values.items() if k not in ("geo_lat", "geo_lng")},
        geo_lat=_geo(values["geo_lat"]),
        geo_lng=_geo(values["geo_lng"]),
        is_active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(location)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _code_conflict(values["code"]) from exc
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=location.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_snapshot(location),
    )
    db.commit()
    db.refresh(location)
    return location


def new_first_location(db: Session, *, dealership: Dealership, actor_id: uuid.UUID | None) -> Location:
    """FR-A-03: a new Betrieb's first location, from its registered seat —
    so the Locations screen is never empty and the rule holds from the first
    day. No code (the dealer chooses it, once) and no hours yet. Flushes;
    the caller's dealership write owns the transaction."""

    default = holiday_service.default_calendar_for_canton(db, dealership.address_canton)
    location = Location(
        tenant_id=dealership.id,
        name=dealership.trade_name or dealership.legal_name,
        location_types=[],
        address_street=dealership.address_street,
        address_house_number=dealership.address_house_number,
        address_postal_code=dealership.address_postal_code,
        address_locality=dealership.address_locality,
        address_canton=dealership.address_canton,
        address_country=dealership.address_country,
        phone=dealership.phone,
        holiday_calendar_id=default.id if default is not None else None,
        is_active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(location)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=location.id,
        tenant_id=dealership.id,
        action="create",
        actor_id=actor_id,
        after=_snapshot(location),
        reason="FR-A-03: the first location, from the registered seat",
    )
    return location


def update_location(db: Session, *, location: Location, data: LocationUpdate, actor_id: uuid.UUID) -> Location:
    changes = data.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] != location.code and location.code is not None:
        raise UnprocessableEntityError(
            "A location's code is fixed once saved — documents and marketplace mappings carry it.",
            details={"reason": "location_code_immutable", "code": location.code},
        )
    if "location_types" in changes:
        changes["location_types"] = [t.value if hasattr(t, "value") else t for t in changes["location_types"]]
    current = {field: getattr(location, field) for field in _SNAPSHOT_FIELDS}
    current["geo_lat"] = None if location.geo_lat is None else float(location.geo_lat)
    current["geo_lng"] = None if location.geo_lng is None else float(location.geo_lng)
    merged = {**current, **changes}

    # The address block is judged only when the PATCH touches it: a row from
    # before KAN-204 with a locality alone keeps saving its other fields.
    if not any(f in changes for f in _ADDRESS_FIELDS):
        merged_address = {f: current[f] for f in _ADDRESS_FIELDS}
        merged.update({f: None for f in _ADDRESS_FIELDS})
        _assert_consistent(db, dealership_id=location.tenant_id, values=merged)
        merged.update(merged_address)
    else:
        _assert_consistent(db, dealership_id=location.tenant_id, values=merged)
        for field in _ADDRESS_FIELDS:
            changes[field] = merged[field]

    before = _snapshot(location)
    if changes.get("is_active") is False and location.is_active:
        _assert_not_last_active(db, location=location)
    for field, value in changes.items():
        if field in ("geo_lat", "geo_lng"):
            value = _geo(value)
        setattr(location, field, value)
    location.updated_by = actor_id
    location.version += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _code_conflict(changes.get("code")) from exc
    diff_before, diff_after = _diff(before, _snapshot(location))
    if diff_before or diff_after:
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=location.id,
            tenant_id=location.tenant_id,
            action="deactivate" if diff_after.get("is_active") is False else "update",
            actor_id=actor_id,
            before=diff_before or None,
            after=diff_after or None,
        )
    db.commit()
    db.refresh(location)
    return location


def _assert_not_last_active(db: Session, *, location: Location) -> None:
    # The dealership row lock serialises deactivations per Betrieb: two
    # concurrent requests for the last two active sites cannot both pass.
    get_dealership_for_update_or_404(db, location.tenant_id)
    others = db.scalar(
        select(func.count())
        .select_from(Location)
        .where(Location.tenant_id == location.tenant_id, Location.is_active.is_(True), Location.id != location.id)
    )
    if not others:
        raise ConflictError(
            "A business always keeps at least one active location (FR-A-03) — activate or create another first.",
            details={"reason": "last_active_location"},
        )


def location_ids_using_bank_account(db: Session, *, account_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(Location.id).where(Location.bank_account_override_id == account_id)).all())


# --- Opening hours ------------------------------------------------------------------------


def list_opening_hours(db: Session, *, location_id: uuid.UUID) -> list[LocationOpeningHours]:
    rows = db.scalars(select(LocationOpeningHours).where(LocationOpeningHours.location_id == location_id)).all()
    order = {d: i for i, d in enumerate(_DEPARTMENTS)}
    return sorted(rows, key=lambda r: (order[r.department], r.weekday))


def _week(rows: list[LocationOpeningHours], department: LocationDepartment) -> list[tuple[Any, ...]]:
    return sorted(
        (r.weekday, r.opens_at, r.closes_at, r.second_opens_at, r.second_closes_at)
        for r in rows
        if r.department == department
    )


def hours_differ_per_department(rows: list[LocationOpeningHours]) -> bool:
    first = _week(rows, _DEPARTMENTS[0])
    return any(_week(rows, d) != first for d in _DEPARTMENTS[1:])


def _hours_snapshot(rows: list[LocationOpeningHours]) -> list[dict[str, Any]]:
    return [
        {
            "department": r.department.value,
            "weekday": r.weekday,
            "opens_at": _plain(r.opens_at),
            "closes_at": _plain(r.closes_at),
            "second_opens_at": _plain(r.second_opens_at),
            "second_closes_at": _plain(r.second_closes_at),
        }
        for r in rows
    ]


def set_opening_hours(
    db: Session, *, location: Location, data: OpeningHoursUpdate, actor_id: uuid.UUID
) -> list[LocationOpeningHours]:
    """Replaces the site's week under the location's lock and version; the
    one-set mode writes each day for every department."""

    before_rows = list_opening_hours(db, location_id=location.id)
    before = _hours_snapshot(before_rows)
    for row in before_rows:
        db.delete(row)
    db.flush()
    for day in data.hours:
        departments = (day.department,) if day.department is not None else _DEPARTMENTS
        for department in departments:
            db.add(
                LocationOpeningHours(
                    location_id=location.id,
                    department=department,
                    weekday=day.weekday,
                    opens_at=day.opens_at,
                    closes_at=day.closes_at,
                    second_opens_at=day.second_opens_at,
                    second_closes_at=day.second_closes_at,
                )
            )
    location.updated_by = actor_id
    location.version += 1
    db.flush()
    rows = list_opening_hours(db, location_id=location.id)
    after = _hours_snapshot(rows)
    if before != after:
        record_audit_event(
            db,
            entity_type=_HOURS_ENTITY,
            entity_id=location.id,
            tenant_id=location.tenant_id,
            action="update",
            actor_id=actor_id,
            before={"per_department": hours_differ_per_department(before_rows), "hours": before},
            after={"per_department": data.per_department, "hours": after},
        )
    db.commit()
    db.refresh(location)
    return list_opening_hours(db, location_id=location.id)


# --- Closures ---------------------------------------------------------------------------------


def _closure_snapshot(closure: LocationClosure) -> dict[str, Any]:
    return {
        "location_id": str(closure.location_id),
        "starts_on": closure.starts_on.isoformat(),
        "ends_on": closure.ends_on.isoformat(),
        "reason": closure.reason,
    }


def list_closures(db: Session, *, location_id: uuid.UUID) -> list[LocationClosure]:
    return list(
        db.scalars(
            select(LocationClosure)
            .where(LocationClosure.location_id == location_id)
            .order_by(LocationClosure.starts_on, LocationClosure.id)
        ).all()
    )


def get_closure_or_404(
    db: Session, *, location_id: uuid.UUID, closure_id: uuid.UUID, for_update: bool = False
) -> LocationClosure:
    stmt = select(LocationClosure).where(LocationClosure.location_id == location_id, LocationClosure.id == closure_id)
    if for_update:
        stmt = stmt.with_for_update()
    closure = db.scalar(stmt)
    if closure is None:
        raise NotFoundError(f"Closure {closure_id} was not found.")
    return closure


def create_closure(db: Session, *, location: Location, data: ClosureCreate, actor_id: uuid.UUID) -> LocationClosure:
    closure = LocationClosure(
        location_id=location.id,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        reason=data.reason.strip(),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(closure)
    db.flush()
    record_audit_event(
        db,
        entity_type=_CLOSURE_ENTITY,
        entity_id=closure.id,
        tenant_id=location.tenant_id,
        action="create",
        actor_id=actor_id,
        after=_closure_snapshot(closure),
    )
    db.commit()
    db.refresh(closure)
    return closure


def update_closure(
    db: Session, *, location: Location, closure: LocationClosure, data: ClosureUpdate, actor_id: uuid.UUID
) -> LocationClosure:
    changes = data.model_dump(exclude_unset=True)
    starts_on = changes.get("starts_on", closure.starts_on)
    ends_on = changes.get("ends_on", closure.ends_on)
    if ends_on < starts_on:
        raise UnprocessableEntityError(
            "endsOn must not lie before startsOn.", details={"reason": "closure_period_inverted"}
        )
    before = _closure_snapshot(closure)
    for field, value in changes.items():
        setattr(closure, field, value.strip() if field == "reason" else value)
    closure.updated_by = actor_id
    closure.version += 1
    db.flush()
    diff_before, diff_after = _diff(before, _closure_snapshot(closure))
    if diff_before or diff_after:
        record_audit_event(
            db,
            entity_type=_CLOSURE_ENTITY,
            entity_id=closure.id,
            tenant_id=location.tenant_id,
            action="update",
            actor_id=actor_id,
            before=diff_before or None,
            after=diff_after or None,
        )
    db.commit()
    db.refresh(closure)
    return closure


def delete_closure(db: Session, *, location: Location, closure: LocationClosure, actor_id: uuid.UUID) -> None:
    """A closure is the dealer's own plan, not a record documents reference —
    a mistaken or cancelled one is removed (audited), unlike a location."""

    record_audit_event(
        db,
        entity_type=_CLOSURE_ENTITY,
        entity_id=closure.id,
        tenant_id=location.tenant_id,
        action="delete",
        actor_id=actor_id,
        before=_closure_snapshot(closure),
    )
    db.delete(closure)
    db.commit()
