"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate). Lives
outside all ten context packages, like app.main and app.db — nothing but
this composition role.
"""

from app.core.audit_model import AuditEvent
from app.core.daily_scheduler_model import DailyJobRun
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun
from app.customer.models.customer import Customer, CustomerAddress, CustomerTag
from app.customer.models.legal_basis import LegalBasis
from app.customer.models.vehicle_party import VehicleParty
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.notification import IntegrationNotification
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.stock_item import StockItem, StockNumberSequence
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_option import StockItemOption
from app.inventory.models.stock_item_publishing import StockItemMedia, StockItemPublishing
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthToken
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.models.user import User
from app.platform.models.user_location import UserLocation
from app.platform.models.user_preference import UserPreference
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal, SalesNumberSequence
from app.sales.models.document import SalesDocument
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction
from app.valuation.models.valuation import Valuation, ValuationDeduction, ValuationNumberSequence
from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    TypeApproval,
    VariantOption,
    VariantOptionEquipmentFeature,
    VariantPrice,
    VariantTypeApproval,
)
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, ProviderSyncState, TyreSpecCache
from app.vehicle.models.configuration import (
    VehicleConfiguration,
    VehicleConfigurationOption,
    VehicleConfigurationOptionFeature,
)
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate, VehiclePlateConflict
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.models.provider import (
    MappingGap,
    ProviderCodeMap,
    ProviderEntityRef,
    VariantOptionProviderCode,
    VariantProviderCode,
)
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as VehicleMdmCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm, VehicleNumberSequence

__all__ = [
    "AuditEvent",
    "AuthEvent",
    "AuthSession",
    "AuthToken",
    "Brand",
    "ColourCache",
    "Credential",
    "Customer",
    "CustomerAddress",
    "CustomerTag",
    "DailyJobRun",
    "DealerGroup",
    "DealerPlate",
    "DealerPlateAssignment",
    "Dealership",
    "DealershipAuthConfig",
    "DealershipMembership",
    "DocumentTemplate",
    "IdempotencyRecord",
    "ImageRef",
    "IntegrationCallLog",
    "IntegrationCallPayload",
    "IntegrationConnection",
    "IntegrationEntitlement",
    "IntegrationNotification",
    "IntegrationProvider",
    "IntegrationSecretRef",
    "InventoryCancelledContract",
    "LegalBasis",
    "Location",
    "MappingGap",
    "MfaPasskey",
    "MfaRecoveryCode",
    "MfaTotp",
    "ModelGroup",
    "ModelVariant",
    "ModelVariantEnergyRating",
    "ModuleEntitlement",
    "OutboxMessage",
    "PlateLookupCacheEntry",
    "ProcessedEvent",
    "ProviderCodeMap",
    "ProviderEntityRef",
    "ProviderSyncState",
    "ReconciliationOrphan",
    "ReconciliationRun",
    "ReferenceList",
    "ReferenceValue",
    "SalesContract",
    "SalesDeal",
    "SalesDocument",
    "SalesLineItem",
    "SalesNumberSequence",
    "SalesOffer",
    "SalesStockItemPurchase",
    "StandardTextDelegation",
    "StockItem",
    "StockItemLedger",
    "StockItemMedia",
    "StockItemOption",
    "StockItemPublishing",
    "StockNumberSequence",
    "Transaction",
    "TypeApproval",
    "TyreSpecCache",
    "User",
    "UserLocation",
    "UserPreference",
    "Valuation",
    "ValuationDeduction",
    "ValuationNumberSequence",
    "VariantOption",
    "VariantOptionEquipmentFeature",
    "VariantOptionProviderCode",
    "VariantPrice",
    "VariantProviderCode",
    "VariantTypeApproval",
    "Vehicle",
    "VehicleAccessory",
    "VehicleConfiguration",
    "VehicleConfigurationOption",
    "VehicleConfigurationOptionFeature",
    "VehicleCustodyEvent",
    "VehicleMdm",
    "VehicleMdmCustodyEvent",
    "VehicleNumberSequence",
    "VehicleOdometerReading",
    "VehicleParty",
    "VehiclePlate",
    "VehiclePlateConflict",
]
