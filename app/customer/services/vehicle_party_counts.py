"""KAN-121 (rule 2): how many vehicle-party rows point at a set of vehicle
ids — asked by vehicle's legacy-reference check
(`app/vehicle/reconciliation.py::count_unrepointed_legacy_vehicle_party_references`,
WP-5 PR-7), which used to count customer's table itself."""

import uuid
from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.customer.models.vehicle_party import VehicleParty


def count_vehicle_parties_for_vehicles(db: Session, *, vehicle_ids: Collection[uuid.UUID]) -> int:
    """Every row, open, closed or kept by a merge (KAN-177), in every dealer
    group: the question is whether any party row still names one of these
    vehicle ids at all. Zero for an empty set, without a query."""

    if not vehicle_ids:
        return 0
    return (
        db.scalar(select(func.count()).select_from(VehicleParty).where(VehicleParty.vehicle_id.in_(list(vehicle_ids))))
        or 0
    )
