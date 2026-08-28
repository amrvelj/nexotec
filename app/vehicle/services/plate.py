"""Plate service layer (WP-5 PR-4).

Every lookup here is TARGETED — takes an exact plate + canton and returns
at most the rows that match — never a list/browse/paginate-all-plates
function. That is not a convention to remember; it is the only shape this
module offers (see tests/architecture/test_plate_lookup_is_not_enumerable.py).
"""

import datetime as dt
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate, VehiclePlateConflict


def _is_current(row: VehiclePlate, as_of: dt.date) -> bool:
    return row.valid_from <= as_of and (row.valid_to is None or row.valid_to >= as_of)


def record_plate_assignment(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    plate: str,
    canton: str,
    valid_from: dt.date,
    valid_to: dt.date | None,
    is_interchangeable: bool,
    plate_group_id: uuid.UUID | None,
    recording_tenant_id: uuid.UUID,
) -> VehiclePlate:
    """Records a plate assignment and checks it against every OTHER
    current assignment of the same plate+canton. Two rows sharing a
    plate_group_id (a Wechselschild) never raise a conflict, by
    construction — the check below only looks at rows WITHOUT a shared
    group. This is ADR-039's central rule, enforced here rather than left
    to convention.
    """

    row = VehiclePlate(
        vehicle_id=vehicle_id,
        plate=plate,
        canton=canton,
        valid_from=valid_from,
        valid_to=valid_to,
        is_interchangeable=is_interchangeable,
        plate_group_id=plate_group_id,
        recording_tenant_id=recording_tenant_id,
    )
    db.add(row)
    db.flush()

    today = utcnow().date()
    others = db.scalars(
        select(VehiclePlate).where(
            VehiclePlate.plate == plate,
            VehiclePlate.canton == canton,
            VehiclePlate.id != row.id,
            VehiclePlate.vehicle_id != vehicle_id,
        )
    ).all()
    for other in others:
        if not _is_current(other, today):
            continue
        shares_group = plate_group_id is not None and other.plate_group_id == plate_group_id
        if shares_group:
            continue  # a legitimate Wechselschild pair — never a conflict
        already_flagged = db.scalar(
            select(VehiclePlateConflict).where(
                VehiclePlateConflict.resolved.is_(False),
                or_(
                    and_(
                        VehiclePlateConflict.first_plate_id == row.id,
                        VehiclePlateConflict.second_plate_id == other.id,
                    ),
                    and_(
                        VehiclePlateConflict.first_plate_id == other.id,
                        VehiclePlateConflict.second_plate_id == row.id,
                    ),
                ),
            )
        )
        if already_flagged is None:
            db.add(
                VehiclePlateConflict(
                    plate=plate, canton=canton, first_plate_id=row.id, second_plate_id=other.id
                )
            )

    db.commit()
    db.refresh(row)
    return row


def resolve_plate(
    db: Session, *, plate: str, canton: str, as_of: dt.date | None = None, include_historical: bool = False
) -> list[VehiclePlate]:
    """Targeted resolve — the ONLY way to reach vehicle_plate rows. Default
    is "valid today"; include_historical is an explicit, separate opt-in,
    never the default (FR-V-06). Returns 0, 1, or 2+ rows — the caller
    (PR-6/PR-9) decides how to render that: a single hit, a Wechselschild
    pair (shared plate_group_id), or a genuine conflict pair.
    """

    as_of = as_of or utcnow().date()
    stmt = select(VehiclePlate).where(VehiclePlate.plate == plate, VehiclePlate.canton == canton)
    if not include_historical:
        stmt = stmt.where(
            VehiclePlate.valid_from <= as_of,
            or_(VehiclePlate.valid_to.is_(None), VehiclePlate.valid_to >= as_of),
        )
    return list(db.scalars(stmt).all())


def list_plates_for_vehicle(db: Session, *, vehicle_id: uuid.UUID) -> list[VehiclePlate]:
    """FR-V-16's Vehicle 360 Plates tab: this vehicle's OWN plate history,
    keyed by a known vehicle id — not the enumerable "browse every plate"
    the rest of this module forbids. Knowing which car you're looking at
    is exactly the identifier the enumerability rule requires; this is
    the targeted lookup, from the other direction.
    """

    return list(
        db.scalars(
            select(VehiclePlate).where(VehiclePlate.vehicle_id == vehicle_id).order_by(VehiclePlate.valid_from.desc())
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
