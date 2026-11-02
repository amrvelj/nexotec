"""Plate service layer (WP-5 PR-4; the write paths since KAN-467).

Every lookup here is TARGETED — takes an exact plate + canton and returns
at most the rows that match — never a list/browse/paginate-all-plates
function. That is not a convention to remember; it is the only shape this
module offers (see tests/architecture/test_plate_lookup_is_not_enumerable.py).

KAN-467 (ADR-039 as amended, Anto 2026-10-31): the register is the DEALER
GROUP's. Every read and write names the caller's `group_id` (from the
token) — another group's plate-to-car link is
never read, matched or moved. Inside the group a Kontrollschild is current on
one car at a time, or on the two cars of a Wechselschild: `record_plate`
refuses a plate that is current on another car of the group and names that
car (409 `plate_current_on_other_vehicle`), and records it once the caller
confirms the move (`move_from`, each other assignment at the version the
refusal showed — it ends the day before the new one starts; rows are never
deleted) or forms the Wechselschild (`wechselschild_with`, likewise).

Audit rows of a write carry the validity and the plate group, never the plate
value: the audit log is listable by a manager, and plate values in it would
make the register enumerable through the back door (ADR-039, ADR-085).

Nothing here commits: the route does, so a refusal leaves nothing behind.
"""

import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.config import get_settings
from app.core.constants import CANTON_CODES
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.uuid7 import uuid7
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate
from app.vehicle.models.vehicle_mdm import VehicleMdm

PLATE_ENTITY_TYPE = "vehicle_plate"
ACTION_PLATE_RECORDED = "plate_recorded"
ACTION_PLATE_ENDED = "plate_ended"
ACTION_PLATE_GROUPED = "plate_wechselschild_formed"

def _clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def swiss_today() -> dt.date:
    """The calendar day in Switzerland (`session_timezone`) — the day a plate
    is "current" on. Never the UTC day, which is still yesterday between
    midnight and 01:00/02:00 Swiss time: a plate recorded from today would
    not be found until then."""

    return _clock().astimezone(ZoneInfo(get_settings().session_timezone)).date()


@dataclass(frozen=True)
class PlateVersionRef:
    """One other car's assignment the user answered about, at the version the
    refusal showed — the move's and the Wechselschild's If-Match."""

    plate_id: uuid.UUID
    version: int


# A Kontrollschild's number: one to six digits, no leading zero.
_PLATE_NUMBER_RE = re.compile(r"^[1-9]\d{0,5}$")


def normalise_kontrollschild(*, canton: str, plate: str) -> tuple[str, str]:
    """(canton, plate) in the one spelling the register stores: the canton
    code upper-cased, the plate as «ZH 123456» — what the search box's
    plate rung resolves (`services/search.py`). The plate may be typed with
    or without its canton prefix, with spaces, dots or dashes. 422
    `plate_canton_invalid` / `plate_invalid` otherwise."""

    code = canton.strip().upper()
    if code not in CANTON_CODES:
        raise UnprocessableEntityError(
            f"'{canton}' is not a Swiss canton code.", details={"reason": "plate_canton_invalid"}
        )
    compact = re.sub(r"[\s.\-]", "", plate.upper())
    compact = compact.removeprefix(code)
    if not _PLATE_NUMBER_RE.match(compact):
        raise UnprocessableEntityError(
            "A Kontrollschild is the canton and one to six digits.", details={"reason": "plate_invalid"}
        )
    return code, f"{code} {compact}"


def _try_normalise(*, canton: str, plate: str) -> tuple[str, str] | None:
    try:
        return normalise_kontrollschild(canton=canton, plate=plate)
    except UnprocessableEntityError:
        return None


def _overlaps(row: VehiclePlate, *, valid_from: dt.date, valid_to: dt.date | None) -> bool:
    starts_before_new_ends = valid_to is None or row.valid_from <= valid_to
    ends_after_new_starts = row.valid_to is None or row.valid_to >= valid_from
    return starts_before_new_ends and ends_after_new_starts


def _lock_plate(db: Session, *, group_id: uuid.UUID, plate: str, canton: str) -> None:
    """Serialises writers of one plate in one group (pg_advisory_xact_lock,
    released at commit), so two concurrent recordings cannot both see the
    plate free. SQLite (the fast lane, ADR-011) serialises writers itself."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"vehicle_plate:{group_id}:{canton}:{plate}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def _audit_after(row: VehiclePlate) -> dict[str, object]:
    return {
        "vehicleId": str(row.vehicle_id),
        "validFrom": row.valid_from.isoformat(),
        "validTo": row.valid_to.isoformat() if row.valid_to else None,
        "isInterchangeable": row.is_interchangeable,
        "plateGroupId": str(row.plate_group_id) if row.plate_group_id else None,
    }


def _holder_details(db: Session, rows: Sequence[VehiclePlate]) -> list[dict[str, object]]:
    """The other cars of the group a plate is current on — the user's own
    group's register, so every one of them may be named (Anto, 2026-10-31)."""

    details: list[dict[str, object]] = []
    for row in rows:
        vehicle = db.get(VehicleMdm, row.vehicle_id)
        details.append(
            {
                "plateId": str(row.id),
                "vehicleId": str(row.vehicle_id),
                "vehicleNumber": vehicle.vehicle_number if vehicle else None,
                "vin": vehicle.vin if vehicle else None,
                "validFrom": row.valid_from.isoformat(),
                "isInterchangeable": row.is_interchangeable,
                "version": row.version,
            }
        )
    return details


def _refuse_unanswered_move(
    db: Session, *, holders: Sequence[VehiclePlate], move_from: Sequence[PlateVersionRef], valid_from: dt.date
) -> dt.date:
    """The move's rules, before anything is written: the caller answered
    exactly about the cars the plate is current on (409
    `plate_current_on_other_vehicle`, naming them), each at the version it
    was shown (a stale one is the If-Match 409), and none of their
    assignments starts on or after the new one (409
    `plate_move_before_start`). Returns the day the moved assignments end."""

    answered = {ref.plate_id: ref.version for ref in move_from}
    if {h.id for h in holders} != set(answered):
        raise ConflictError(
            "This plate is current on another vehicle; confirm the move or form a Wechselschild.",
            details={"reason": "plate_current_on_other_vehicle", "holders": _holder_details(db, holders)},
        )
    ends_on = valid_from - dt.timedelta(days=1)
    for holder in holders:
        check_version(holder.version, answered[holder.id], entity_name="Plate assignment")
        if ends_on < holder.valid_from:
            raise ConflictError(
                "The other vehicle's assignment starts on or after this one; it cannot be moved from.",
                details={"reason": "plate_move_before_start", "holders": _holder_details(db, [holder])},
            )
    return ends_on


@dataclass(frozen=True)
class PlateRecordingCheck:
    """What `check_plate_recording` found: the plate in the register's one
    spelling, and whether this car already carries it over that validity
    (then a recording would add nothing)."""

    canton: str
    plate: str
    already_on_vehicle: bool


def check_plate_recording(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    group_id: uuid.UUID,
    plate: str,
    canton: str,
    valid_from: dt.date,
    move_from: Sequence[PlateVersionRef] = (),
) -> PlateRecordingCheck:
    """KAN-523 — `record_plate`'s refusals, asked before a caller commits to
    a recording it will only make later (Sales' «Rechnung anfordern»: the
    plate is recorded by the vehicle consumer of
    `sales.contract.invoice_requested`). Reads only: the same spelling (422),
    and for a plate current on another car of the group the same 409
    `plate_current_on_other_vehicle` naming it until `move_from` answers
    for exactly those cars at their versions. A plate this car already
    carries over the validity is no refusal here — the recording would be a
    no-op, as a second invoice request for the same car is. No Wechselschild:
    a car handed over leaves with its own plate."""

    canton, plate = normalise_kontrollschild(canton=canton, plate=plate)
    rows = db.scalars(
        select(VehiclePlate).where(
            VehiclePlate.group_id == group_id, VehiclePlate.plate == plate, VehiclePlate.canton == canton
        )
    ).all()
    overlapping = [r for r in rows if _overlaps(r, valid_from=valid_from, valid_to=None)]
    if any(r.vehicle_id == vehicle_id for r in overlapping):
        return PlateRecordingCheck(canton=canton, plate=plate, already_on_vehicle=True)
    holders = sorted((r for r in overlapping if r.vehicle_id != vehicle_id), key=lambda r: r.valid_from)
    if holders:
        _refuse_unanswered_move(db, holders=holders, move_from=move_from, valid_from=valid_from)
    return PlateRecordingCheck(canton=canton, plate=plate, already_on_vehicle=False)


def record_plate(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    group_id: uuid.UUID,
    recording_tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    plate: str,
    canton: str,
    valid_from: dt.date,
    valid_to: dt.date | None = None,
    move_from: Sequence[PlateVersionRef] = (),
    wechselschild_with: PlateVersionRef | None = None,
) -> VehiclePlate:
    """Records a Kontrollschild on a vehicle in the group's register.

    The plate may not be current on another car of the group over the new
    assignment's validity, unless the caller confirms one of the two ways
    out the refusal offers:

    - `move_from` names exactly the assignments the 409 listed, each at the
      version it showed: each ends the day before `valid_from` (ADR-039's
      "the plate moves");
    - `wechselschild_with` names the one other car's assignment at its
      version: the two form a Wechselschild (one plate group, at most two).

    The refusals name the group's other cars (`details.holders`): a plate
    read. The route passes them through the plate-read guard (audited and
    limited, ADR-085) before they reach the caller; a caller that is not a
    person (an import) never shows them.
    """

    canton, plate = normalise_kontrollschild(canton=canton, plate=plate)
    if valid_to is not None and valid_to < valid_from:
        raise UnprocessableEntityError(
            "An assignment cannot end before it starts.", details={"reason": "plate_validity_invalid"}
        )
    if move_from and wechselschild_with is not None:
        raise UnprocessableEntityError(
            "A plate either moves or forms a Wechselschild, not both.",
            details={"reason": "plate_move_and_wechselschild"},
        )
    _lock_plate(db, group_id=group_id, plate=plate, canton=canton)

    rows = db.scalars(
        select(VehiclePlate).where(
            VehiclePlate.group_id == group_id, VehiclePlate.plate == plate, VehiclePlate.canton == canton
        )
    ).all()
    overlapping = [r for r in rows if _overlaps(r, valid_from=valid_from, valid_to=valid_to)]
    if any(r.vehicle_id == vehicle_id for r in overlapping):
        raise ConflictError(
            "This plate is already recorded on this vehicle for that period.",
            details={"reason": "plate_already_on_vehicle"},
        )
    holders = sorted((r for r in overlapping if r.vehicle_id != vehicle_id), key=lambda r: r.valid_from)

    row = VehiclePlate(
        vehicle_id=vehicle_id,
        group_id=group_id,
        plate=plate,
        canton=canton,
        valid_from=valid_from,
        valid_to=valid_to,
        is_interchangeable=False,
        plate_group_id=None,
        recording_tenant_id=recording_tenant_id,
    )

    if wechselschild_with is not None:
        partner = next((h for h in holders if h.id == wechselschild_with.plate_id), None)
        if partner is None:
            # Not the car the refusal named: say nothing about who holds the
            # plate (a guessed id must never become a plate lookup).
            raise ConflictError(
                "A Wechselschild joins this vehicle to the other vehicle the plate is current on.",
                details={"reason": "plate_wechselschild_not_possible"},
            )
        if len(holders) != 1:
            # A Wechselschild is one plate on two cars: the plate is already
            # on two.
            raise ConflictError(
                "A Wechselschild joins this vehicle to the one other vehicle the plate is current on.",
                details={"reason": "plate_wechselschild_not_possible", "holders": _holder_details(db, holders)},
            )
        check_version(partner.version, wechselschild_with.version, entity_name="Plate assignment")
        before = _audit_after(partner)
        partner.plate_group_id = partner.plate_group_id or uuid7()
        partner.is_interchangeable = True
        partner.version += 1
        record_audit_event(
            db, entity_type=PLATE_ENTITY_TYPE, entity_id=partner.id, tenant_id=recording_tenant_id,
            action=ACTION_PLATE_GROUPED, actor_id=actor_id, before=before, after=_audit_after(partner),
        )
        row.plate_group_id = partner.plate_group_id
        row.is_interchangeable = True
    elif holders:
        ends_on = _refuse_unanswered_move(db, holders=holders, move_from=move_from, valid_from=valid_from)
        for holder in holders:
            before = _audit_after(holder)
            holder.valid_to = ends_on
            holder.version += 1
            record_audit_event(
                db, entity_type=PLATE_ENTITY_TYPE, entity_id=holder.id, tenant_id=recording_tenant_id,
                action=ACTION_PLATE_ENDED, actor_id=actor_id, before=before, after=_audit_after(holder),
                reason=f"moved to vehicle {vehicle_id}",
            )

    db.add(row)
    db.flush()
    record_audit_event(
        db, entity_type=PLATE_ENTITY_TYPE, entity_id=row.id, tenant_id=recording_tenant_id,
        action=ACTION_PLATE_RECORDED, actor_id=actor_id, after=_audit_after(row),
    )
    return row


def get_vehicle_plate_or_404(
    db: Session, *, vehicle_id: uuid.UUID, plate_id: uuid.UUID, group_id: uuid.UUID
) -> VehiclePlate:
    """One assignment of a known vehicle in the caller's group's register —
    404 for another group's row, as for one that does not exist."""

    row = db.get(VehiclePlate, plate_id)
    if row is None or row.vehicle_id != vehicle_id or row.group_id != group_id:
        raise NotFoundError(f"Plate assignment {plate_id} was not found.")
    return row


def end_plate(
    db: Session,
    *,
    row: VehiclePlate,
    valid_to: dt.date,
    expected_version: int,
    actor_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
) -> VehiclePlate:
    """Ends an assignment on `valid_to` (FR-V-16 «beenden»); the row stays.
    A Wechselschild partner keeps its own assignment. Under the plate's lock,
    re-read: a move of the same plate committing meanwhile is seen here, and
    the version check below refuses the stale «beenden»."""

    _lock_plate(db, group_id=row.group_id, plate=row.plate, canton=row.canton)
    db.refresh(row)
    check_version(row.version, expected_version, entity_name="Plate assignment")
    if valid_to < row.valid_from:
        raise UnprocessableEntityError(
            "An assignment cannot end before it starts.", details={"reason": "plate_validity_invalid"}
        )
    if row.valid_to is not None and row.valid_to <= valid_to:
        raise ConflictError("This assignment has already ended.", details={"reason": "plate_already_ended"})
    before = _audit_after(row)
    row.valid_to = valid_to
    row.version += 1
    record_audit_event(
        db, entity_type=PLATE_ENTITY_TYPE, entity_id=row.id, tenant_id=tenant_id,
        action=ACTION_PLATE_ENDED, actor_id=actor_id, before=before, after=_audit_after(row),
    )
    db.flush()
    return row


def resolve_plate(
    db: Session,
    *,
    group_id: uuid.UUID,
    plate: str,
    canton: str,
    as_of: dt.date | None = None,
    include_historical: bool = False,
) -> list[VehiclePlate]:
    """Targeted resolve in one group's register — the ONLY way to reach
    vehicle_plate rows by plate. Default is "valid today"; include_historical
    is an explicit, separate opt-in, never the default (FR-V-06). Returns 0,
    1, or 2 rows (a Wechselschild pair); a value that is no Kontrollschild
    finds nothing.
    """

    normalised = _try_normalise(canton=canton, plate=plate)
    if normalised is None:
        return []
    canton, plate = normalised
    as_of = as_of or swiss_today()
    stmt = select(VehiclePlate).where(
        VehiclePlate.group_id == group_id, VehiclePlate.plate == plate, VehiclePlate.canton == canton
    )
    if not include_historical:
        stmt = stmt.where(
            VehiclePlate.valid_from <= as_of,
            or_(VehiclePlate.valid_to.is_(None), VehiclePlate.valid_to >= as_of),
        )
    return list(db.scalars(stmt).all())


def list_plates_for_vehicle(db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID) -> list[VehiclePlate]:
    """FR-V-16's Vehicle 360 Plates tab: this vehicle's OWN plate history in
    the caller's group's register, keyed by a known vehicle id — not the
    enumerable "browse every plate" the rest of this module forbids. Only
    `plate_read_guard.read_plate_history` calls it (audited and limited).
    """

    return list(
        db.scalars(
            select(VehiclePlate)
            .where(VehiclePlate.vehicle_id == vehicle_id, VehiclePlate.group_id == group_id)
            .order_by(VehiclePlate.valid_from.desc())
        ).all()
    )


def current_dealer_plate_assignment(db: Session, *, dealer_plate_id: uuid.UUID) -> DealerPlateAssignment | None:
    return db.scalar(
        select(DealerPlateAssignment).where(
            DealerPlateAssignment.dealer_plate_id == dealer_plate_id, DealerPlateAssignment.valid_to.is_(None)
        )
    )


def assign_dealer_plate(
    db: Session, *, dealer_plate: DealerPlate, vehicle_id: uuid.UUID
) -> DealerPlateAssignment:
    """Moves a Händlerschild onto a new vehicle — closes whatever
    assignment is current (if any) and opens a new one, same
    close-then-open discipline as every other allocation table in this
    project (ContactChannelMixin, and PR-9's party roles).
    """

    current = current_dealer_plate_assignment(db, dealer_plate_id=dealer_plate.id)
    if current is not None:
        current.valid_to = utcnow()

    assignment = DealerPlateAssignment(
        dealer_plate_id=dealer_plate.id, vehicle_id=vehicle_id, tenant_id=dealer_plate.tenant_id
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment
