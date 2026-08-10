"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate). Lives
outside all ten context packages, like app.main and app.db — nothing but
this composition role.
"""

from app.core.audit_model import AuditEvent  # noqa: F401
from app.core.idempotency_model import IdempotencyRecord  # noqa: F401
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun  # noqa: F401
from app.customer.models.customer import Customer  # noqa: F401
from app.customer.models.vehicle_party import VehicleParty  # noqa: F401
from app.platform.models.credential import Credential  # noqa: F401
from app.platform.models.dealer import Dealer  # noqa: F401
from app.platform.models.reference_data import ReferenceList, ReferenceValue  # noqa: F401
from app.platform.models.user import User  # noqa: F401
from app.platform.models.user_preference import UserPreference  # noqa: F401
from app.sales.models.transaction import Transaction  # noqa: F401
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent  # noqa: F401

__all__ = [
    "AuditEvent",
    "Credential",
    "Customer",
    "Dealer",
    "IdempotencyRecord",
    "ReconciliationOrphan",
    "ReconciliationRun",
    "ReferenceList",
    "ReferenceValue",
    "Transaction",
    "User",
    "UserPreference",
    "Vehicle",
    "VehicleCustodyEvent",
    "VehicleParty",
]
