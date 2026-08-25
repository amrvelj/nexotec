"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate). Lives
outside all ten context packages, like app.main and app.db — nothing but
this composition role.
"""

from app.core.audit_model import AuditEvent
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun
from app.customer.models.customer import Customer
from app.customer.models.vehicle_party import VehicleParty
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.user import User
from app.platform.models.user_preference import UserPreference
from app.sales.models.transaction import Transaction
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent

__all__ = [
    "AuditEvent",
    "Credential",
    "Customer",
    "DealerGroup",
    "Dealership",
    "IdempotencyRecord",
    "Location",
    "OutboxMessage",
    "ProcessedEvent",
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
