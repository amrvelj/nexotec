"""A user's sites (KAN-200; Dealer Administration Users → Fields, FR-A-02):
one primary location plus additional ones, replaced as a set. Every id must
name a location of the user's own dealership (422 otherwise — never a
cross-tenant row, and never a 404 that would confirm another Betrieb's
location exists). Flushes, never commits: the caller's user write owns the
transaction (same as text delegations)."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.platform.models.dealership import Location
from app.platform.models.user import User
from app.platform.models.user_location import UserLocation

# "Field not sent" for the PATCH's two location fields (None is a value: no primary).
UNSET: object = object()


def list_locations(db: Session, *, dealership_id: uuid.UUID) -> list[Location]:
    return list(
        db.scalars(
            select(Location).where(Location.tenant_id == dealership_id).order_by(Location.name, Location.id)
        ).all()
    )


def locations_of(
    db: Session, *, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID | None, list[uuid.UUID]]]:
    """(primary, additional) per user, in one query for a page of users."""

    ids = list(user_ids)
    result: dict[uuid.UUID, tuple[uuid.UUID | None, list[uuid.UUID]]] = {uid: (None, []) for uid in ids}
    if not ids:
        return result
    rows = db.execute(
        select(UserLocation.user_id, UserLocation.location_id, UserLocation.is_primary)
        .where(UserLocation.user_id.in_(ids))
        .order_by(UserLocation.user_id, UserLocation.id)
    ).all()
    for user_id, location_id, is_primary in rows:
        _, additional = result[user_id]
        if is_primary:
            result[user_id] = (location_id, additional)
        else:
            additional.append(location_id)
    return result


def set_locations(
    db: Session, *, user: User, primary_location_id: uuid.UUID | None, additional_location_ids: list[uuid.UUID]
) -> bool:
    """Replaces the user's set. Returns whether anything changed."""

    wanted_additional = [lid for lid in dict.fromkeys(additional_location_ids) if lid != primary_location_id]
    wanted = {lid: lid == primary_location_id for lid in wanted_additional}
    if primary_location_id is not None:
        wanted[primary_location_id] = True
    if wanted:
        known = set(
            db.scalars(
                select(Location.id).where(Location.tenant_id == user.tenant_id, Location.id.in_(list(wanted)))
            ).all()
        )
        unknown = sorted(str(lid) for lid in wanted if lid not in known)
        if unknown:
            raise UnprocessableEntityError(
                "Every location must be a location of this dealership.", details={"locationIds": unknown}
            )

    current = {
        row.location_id: row for row in db.scalars(select(UserLocation).where(UserLocation.user_id == user.id)).all()
    }
    changed = False
    for location_id, held in current.items():
        if location_id not in wanted:
            db.delete(held)
            changed = True
    # Clear the old primary before a new one is flagged: the partial unique
    # index sees the flush as one statement sequence.
    for location_id, held in current.items():
        if location_id in wanted and held.is_primary and not wanted[location_id]:
            held.is_primary = False
            changed = True
    db.flush()
    for location_id, is_primary in wanted.items():
        row: UserLocation | None = current.get(location_id)
        if row is None:
            db.add(UserLocation(user_id=user.id, location_id=location_id, is_primary=is_primary))
            changed = True
        elif row.is_primary != is_primary:
            row.is_primary = is_primary
            changed = True
    db.flush()
    return changed
