"""Stock's one way from a VIN to its vehicle-mdm record (KAN-244, KAN-257).

Both writers that put a VIN into stock resolve it here: a direct create
with a VIN (`stock_item.create_stock_item`, KAN-244) and a pipeline item's
promotion (`pipeline.promote_to_vehicle_mdm`, FR-V-04). FR-V-15: a VIN that
already exists is the same car, so the record is found, or created when
vehicle-mdm has never seen the VIN — always through `app.vehicle.public`.

**On a session of its own.** `create_or_get_vehicle_mdm` is a cross-context
write that commits its own transaction (ADR-047), and on a lost insert race
its guard (`create_vehicle_mdm`) calls `rollback()` on the session it is
handed. Handed the caller's session, the first would commit whatever the
caller had pending and the second would silently discard it — the caller
would carry on writing onto a half-undone transaction. Here vehicle-mdm
gets a short-lived session bound to the same database, so it commits (or
rolls back) only its own row, and the caller's transaction is never
touched. The caller receives plain values, not an ORM object of another
context.

**The insert race (KAN-257).** Two writers resolving a VIN vehicle-mdm has
never seen both miss the lookup and both insert; vehicle-mdm's unique
index refuses the second, as a 409 naming the VIN (no `reason`). A unique
violation in Postgres means the other row is committed, so the loser looks
again — the same `create_or_get_vehicle_mdm`, which now finds the winner's
record — and goes on with it. What the caller does next is its own rule:
a winner that took the car into the same dealership's stock makes the
caller's write a `vin_already_in_stock` refusal (KAN-111's index).
"""

import dataclasses
import uuid

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.vehicle.public import create_or_get_vehicle_mdm


@dataclasses.dataclass(frozen=True)
class ResolvedVehicle:
    """The vehicle-mdm record a VIN resolved to — plain data, never the row."""

    id: uuid.UUID
    vin: str


def _lost_the_insert_race(exc: ConflictError, vin: str) -> bool:
    """vehicle-mdm's own refusal of a second row for this VIN — and nothing
    else, so any other conflict still reaches the caller."""

    details = exc.details or {}
    return "reason" not in details and details.get("vin") == vin


def resolve_vehicle_mdm_by_vin(
    db: Session, *, vin: str, catalogue_variant_id: uuid.UUID | None = None, actor_id: uuid.UUID | None = None
) -> ResolvedVehicle:
    """The vehicle-mdm record for `vin`, created if vehicle-mdm has never
    seen it — committed before this returns, on a session of its own
    (module docstring). `vin` must already be valid and normalised."""

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
        return ResolvedVehicle(id=vehicle.id, vin=vehicle.vin)
