"""The only surface other contexts may import from vehicle. Import-linter's
contract allows `app.<other-context>` to import `app.vehicle.public`, never
`app.vehicle.models` / `app.vehicle.services` / `app.vehicle.api` directly.
"""

import uuid

from sqlalchemy.orm import Session

from app.vehicle.models.vehicle import CustodyEventType, Vehicle, VehicleStatus
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.vehicle import create_custody_event, get_vehicle_or_404


# WP-7 PR-1: inventory's StockItem.vehicle_id references VehicleMdm (WP-5's
# three-layer model), not the legacy Vehicle above — the first consumer of
# this half of the surface.
#
# get_vehicle_mdm_or_404 is wrapped, not re-exported directly from
# app.vehicle.services.vehicle_mdm: that module imports app.vehicle.schemas.
# vehicle_mdm (for update_vehicle_mdm's parameter type), which imports
# app.customer.public — and app.customer.models.vehicle_party imports THIS
# module (for the legacy Vehicle/VehicleStatus types above), so a
# module-level import here would be a real import cycle, not just a slow
# one. Deferred to call time, well after both modules have finished
# loading at process startup.
def get_vehicle_mdm_or_404(db: Session, vehicle_id: uuid.UUID) -> VehicleMdm:
    from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404 as _get_vehicle_mdm_or_404

    return _get_vehicle_mdm_or_404(db, vehicle_id)


__all__ = [
    "CustodyEventType",
    "Vehicle",
    "VehicleMdm",
    "VehicleStatus",
    "create_custody_event",
    "get_vehicle_mdm_or_404",
    "get_vehicle_or_404",
]
