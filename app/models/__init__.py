"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate).
"""

from app.models.audit import AuditEvent  # noqa: F401
from app.models.credential import Credential  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.dealer import Dealer  # noqa: F401
from app.models.idempotency import IdempotencyRecord  # noqa: F401
from app.models.reference_data import ReferenceList, ReferenceValue  # noqa: F401
from app.models.transaction import Transaction  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.vehicle import Vehicle, VehicleCustodyEvent, VehicleParty  # noqa: F401

__all__ = [
    "AuditEvent",
    "Credential",
    "Customer",
    "Dealer",
    "IdempotencyRecord",
    "ReferenceList",
    "ReferenceValue",
    "Transaction",
    "User",
    "Vehicle",
    "VehicleCustodyEvent",
    "VehicleParty",
]
