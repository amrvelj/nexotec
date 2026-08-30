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
from app.customer.models.customer import Customer, CustomerAddress
from app.customer.models.legal_basis import LegalBasis
from app.customer.models.vehicle_party import VehicleParty
from app.inventory.models.stock_item import StockItem, StockNumberSequence
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_option import StockItemOption
from app.inventory.models.stock_item_publishing import StockItemMedia, StockItemPublishing
from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.user import User
from app.platform.models.user_preference import UserPreference
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal, SalesNumberSequence
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantOption
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate, VehiclePlateConflict
from app.vehicle.models.provider import MappingGap, ProviderCodeMap, ProviderEntityRef
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as VehicleMdmCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm, VehicleNumberSequence

__all__ = [
    "AuditEvent",
    "Brand",
    "Customer",
    "CustomerAddress",
    "DealerGroup",
    "DealerPlate",
    "DealerPlateAssignment",
    "Dealership",
    "DealershipMembership",
    "IdempotencyRecord",
    "LegalBasis",
    "Location",
    "MappingGap",
    "ModelGroup",
    "ModelVariant",
    "ModelVariantEnergyRating",
    "OutboxMessage",
    "ProcessedEvent",
    "ProviderCodeMap",
    "ProviderEntityRef",
    "ReconciliationOrphan",
    "ReconciliationRun",
    "ReferenceList",
    "ReferenceValue",
    "SalesContract",
    "SalesDeal",
    "SalesLineItem",
    "SalesNumberSequence",
    "SalesOffer",
    "StockItem",
    "StockItemLedger",
    "StockItemMedia",
    "StockItemOption",
    "StockItemPublishing",
    "StockNumberSequence",
    "Transaction",
    "TypeApproval",
    "User",
    "UserPreference",
    "VariantOption",
    "Vehicle",
    "VehicleAccessory",
    "VehicleCustodyEvent",
    "VehicleMdm",
    "VehicleMdmCustodyEvent",
    "VehicleNumberSequence",
    "VehicleOdometerReading",
    "VehicleParty",
    "VehiclePlate",
    "VehiclePlateConflict",
]
