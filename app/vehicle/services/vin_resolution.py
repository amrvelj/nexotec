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
