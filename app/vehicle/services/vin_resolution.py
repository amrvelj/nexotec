"""Another context's one way from a VIN to its vehicle-mdm record (KAN-337).

KAN-257 ruled the own-session resolution for Stock, and it was copied into
the Sales trade-in and the valuation (KAN-328) until KAN-189 retired those
two. It lives here, beside the guard whose refusal it interprets, and is
offered as `app.vehicle.public.resolve_vehicle_mdm_by_vin`, so no caller
keeps a copy of the session handling or of the race predicate.

This module imports nothing of Vehicle's at load time: `app.vehicle.public`
imports `ResolvedVehicleMdm` from here, and `services.vehicle_mdm` is
imported at call time, as `public.get_vehicle_mdm_or_404` defers it. The
customer import cycle that once made this necessary is gone since KAN-634
(vehicle imports nothing of customer's); the deferral is kept, harmless.
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.errors import ConflictError


@dataclasses.dataclass(frozen=True)
class ResolvedVehicleMdm:
    """The vehicle-mdm record a VIN resolved to — plain data, never the row
    (another context gets no ORM object of Vehicle's)."""

    id: uuid.UUID
    vin: str


def _lost_the_insert_race(exc: ConflictError, vin: str) -> bool:
    """`create_vehicle_mdm`'s own refusal of a second row for this VIN — a
    409 naming the VIN and carrying no `reason` — and nothing else, so any
    other conflict still reaches the caller. Kept beside the guard that
    raises it: whoever changes that refusal's shape changes this too."""

    details = exc.details or {}
    return "reason" not in details and details.get("vin") == vin


def resolve_vehicle_mdm_by_vin(
    db: Session, *, vin: str, actor_id: uuid.UUID | None, catalogue_variant_id: uuid.UUID | None = None
) -> ResolvedVehicleMdm:
    """Another context's one way from a VIN to its vehicle-mdm record
    (FR-V-04, FR-V-15: a VIN that already exists is the same car) — found,
    or created when vehicle-mdm has never seen the VIN. KAN-337: the
    own-session resolution KAN-257 ruled for Stock, owned by Vehicle so no
    caller carries a copy of it.

    **On a session of its own.** `create_or_get_vehicle_mdm` commits its own
    transaction (ADR-047), and on a lost insert race its guard calls
    `rollback()` on the session it is handed. Handed the caller's session,
    the first would commit whatever the caller had pending and the second
    would silently discard it. Here vehicle-mdm works on a short-lived
    session bound to the caller's database (`db` is used for its bind only),
    so it commits or rolls back only its own row; the caller's transaction is
    never touched, and the caller receives plain values.

    **The insert race (KAN-257).** Two writers resolving a VIN vehicle-mdm
    has never seen both miss the lookup and both insert; the unique index
    refuses the second. A unique violation in Postgres means the other row
    is committed, so the loser looks again — the same lookup now finds the
    winner's record — and returns it. What the caller does next is its own
    rule (Stock refuses `vin_already_in_stock` when the winner took the car
    into the same dealership).

    Committed before this returns. `vin` must already be valid and
    normalised (`NormalisedVin`); `actor_id` is the user whose request
    created the record (null only for a system write, KAN-243)."""

    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm

    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        try:
            vehicle, _created = create_or_get_vehicle_mdm(
                own, vin=vin, catalogue_variant_id=catalogue_variant_id, actor_id=actor_id
            )
        except ConflictError as exc:
            if not _lost_the_insert_race(exc, vin):
                raise
            # The guard rolled `own` back; the winner's row is committed.
            vehicle, _created = create_or_get_vehicle_mdm(
                own, vin=vin, catalogue_variant_id=catalogue_variant_id, actor_id=actor_id
            )
        return ResolvedVehicleMdm(id=vehicle.id, vin=vehicle.vin)


def find_vehicle_mdm_by_chassis_number(
    db: Session, *, chassis_number: str, make: str | None
) -> ResolvedVehicleMdm | None:
    """KAN-535: the pre-1981 car's chassis-number record for this number and
    make, compared tolerantly (`chassis_make_key`) — a read, nothing created;
    None when there is none (or no make to compare)."""

    from app.vehicle.services.vehicle_mdm import chassis_make_key, get_vehicle_mdm_by_chassis_number

    make_key = chassis_make_key(make)
    if make_key is None:
        return None
    vehicle = get_vehicle_mdm_by_chassis_number(db, chassis_number=chassis_number, make_key=make_key)
    return ResolvedVehicleMdm(id=vehicle.id, vin=vehicle.vin) if vehicle is not None else None


def resolve_vehicle_mdm_by_chassis_number(
    db: Session,
    *,
    chassis_number: str,
    make: str | None,
    first_registration_date: dt.date | None,
    actor_id: uuid.UUID | None,
) -> ResolvedVehicleMdm:
    """KAN-535 (ADR-103 as amended): `resolve_vehicle_mdm_by_vin` for a car
    first registered before 1981 that carries a chassis number — its record
    is that number together with its make (Anto, 2026-11-13, option A),
    found or created, on Vehicle's own session, a lost insert race resolved
    to the winner's record. 422 when the number, the first registration or
    the make does not make a chassis-number record
    (`vehicle_mdm.require_chassis_number_identity`). Committed before this
    returns; `chassis_number` is normalised as a VIN is."""

    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm_by_chassis_number

    def resolve(own: Session) -> ResolvedVehicleMdm:
        vehicle, _created = create_or_get_vehicle_mdm_by_chassis_number(
            own, chassis_number=chassis_number, make=make, first_registration_date=first_registration_date,
            actor_id=actor_id,
        )
        return ResolvedVehicleMdm(id=vehicle.id, vin=vehicle.vin)

    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        try:
            return resolve(own)
        except ConflictError as exc:
            if not _lost_the_insert_race(exc, chassis_number):
                raise
            return resolve(own)  # the guard rolled `own` back; the winner's row is committed
