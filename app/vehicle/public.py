"""The only surface other contexts may import from vehicle. Import-linter's
contract allows `app.<other-context>` to import `app.vehicle.public`, never
`app.vehicle.models` / `app.vehicle.services` / `app.vehicle.api` directly.
"""

from app.vehicle.models.vehicle import CustodyEventType, Vehicle, VehicleStatus
from app.vehicle.services.vehicle import create_custody_event, get_vehicle_or_404

__all__ = [
    "CustodyEventType",
    "Vehicle",
    "VehicleStatus",
    "create_custody_event",
    "get_vehicle_or_404",
]
