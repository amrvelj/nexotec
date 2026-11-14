"""Reads of the merge chain (KAN-706), apart from `merge.py` so a service the
merge itself imports (the identity PATCH's stock-holding read) can use them.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.vehicle_mdm import VehicleMdm


def merged_vehicle_chain(db: Session, vehicle_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """KAN-706 — every record from `vehicle_id` to the one it lives on, in
    chain order (A merged into B, B into C gives (A, B, C)); an id never
    merged, or naming no record, gives itself alone. Read-only, from the
    column (as `resolve_merged_id`). A writer of rows that follow a merge
    asynchronously — customer's party rows, moved by its consumer of
    `vehicle.vehicle_mdm.merged` — reads its rows on every id here, so a fact
    consumed before that move still finds them."""

    chain: list[uuid.UUID] = [vehicle_id]
    while True:
        merged_into = db.execute(
            select(VehicleMdm.merged_into_vehicle_id).where(VehicleMdm.id == chain[-1])
        ).one_or_none()
        if merged_into is None or merged_into[0] is None or merged_into[0] in chain:
            return tuple(chain)
        chain.append(merged_into[0])


def records_living_on(db: Session, vehicle_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """KAN-706 — `vehicle_id` and every record merged into it, directly or
    through a chain (B and A for C, when A was merged into B and B into C).
    What Vehicle's own projections keyed by a record — the stock holding
    (ADR-160), whose unapplied outbox facts still name the record Stock booked
    — count for the live record. Read-only."""

    found: list[uuid.UUID] = [vehicle_id]
    frontier = [vehicle_id]
    while frontier:
        merged = [
            row_id
            for row_id in db.scalars(select(VehicleMdm.id).where(VehicleMdm.merged_into_vehicle_id.in_(frontier)))
            if row_id not in found
        ]
        found.extend(merged)
        frontier = merged
    return tuple(found)
