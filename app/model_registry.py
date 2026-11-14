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
from app.customer.models.customer import Customer, CustomerAddress, CustomerContactGroup, CustomerTag
from app.customer.models.legal_basis import LegalBasis
from app.customer.models.stock_item_booking import StockItemBooking
from app.customer.models.vehicle_party import VehicleParty
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.caption import IntegrationProviderCaption, IntegrationProviderCaptionFetch
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.notification import IntegrationNotification
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.sales_contract import InventorySalesContract, InventorySalesContractBackfill
from app.inventory.models.stock_item import StockItem, StockNumberSequence
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_option import StockItemOption
from app.inventory.models.stock_item_publishing import StockItemMedia, StockItemPublishing
from app.inventory.models.website_listing import StockItemWebsiteListing
from app.reporting.models.public_stock_listing import PublicStockListing
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthToken
from app.platform.models.connected_account import UserConnectedAccount
from app.platform.models.credential import Credential
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.export_job import ExportJob
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.models.notification import (
    Notification,
    NotificationEvent,
    PushSubscription,
    UserNotificationPreference,
)
from app.platform.models.standard_text import StandardText
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.models.user import User
from app.platform.models.user_location import UserLocation
from app.platform.models.user_preference import UserPreference
from app.platform.organisation.models.branding_asset import BrandingAsset
from app.platform.organisation.models.publishing_profile import LocationLeadRouting, PublishingProfile
from app.platform.organisation.models.contact_group import DealershipContactGroup
from app.platform.organisation.models.dealership import DealerGroup, Dealership, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.organisation.models.location_schedule import LocationClosure, LocationOpeningHours
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRange
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.platform.reference.models.ui_translation import MissingTranslation, UiTranslation, UiTranslationRevision
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal, SalesNumberSequence
from app.sales.models.document import SalesDocument
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.valuation.models.contract_carry import ValuationContractCarry
from app.valuation.models.valuation import (
    Valuation,
    ValuationDeduction,
    ValuationNumberSequence,
    ValuationProviderQuote,
)
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
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, TyreSpecCache, VariantDetailState
from app.vehicle.models.configuration import (
    VehicleConfiguration,
    VehicleConfigurationOption,
    VehicleConfigurationOptionFeature,
)
from app.vehicle.models.energy_rating import ModelVariantEnergyLabel, ModelVariantEnergyRating
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.models.provider import (
    MappingGap,
    ProviderCodeMap,
    ProviderEntityRef,
    VariantOptionProviderCode,
    VariantProviderCode,
)
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as VehicleMdmCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm, VehicleNumberSequence

__all__ = [
    "AuditEvent",
    "AuthEvent",
    "AuthSession",
    "AuthToken",
    "Brand",
    "BrandingAsset",
    "LocationLeadRouting",
    "PublishingProfile",
    "ColourCache",
    "Credential",
    "Customer",
    "CustomerAddress",
    "CustomerContactGroup",
    "CustomerTag",
    "DailyJobRun",
    "DealerGroup",
    "DealerPlate",
    "DealerPlateAssignment",
    "Dealership",
    "DealershipAuthConfig",
    "DealershipBankAccount",
    "DealershipContactGroup",
    "DealershipMembership",
    "DealershipSettings",
    "DocumentTemplate",
    "ExportJob",
    "Holiday",
    "HolidayCalendar",
    "IdempotencyRecord",
    "ImageRef",
    "IntegrationCallLog",
    "IntegrationCallPayload",
    "IntegrationConnection",
    "IntegrationEntitlement",
    "IntegrationNotification",
    "IntegrationProvider",
    "IntegrationProviderCaption",
    "IntegrationProviderCaptionFetch",
    "IntegrationSecretRef",
    "InventoryCancelledContract",
    "InventorySalesContract",
    "InventorySalesContractBackfill",
    "LegalBasis",
    "Location",
    "LocationClosure",
    "LocationOpeningHours",
    "MappingGap",
    "MfaPasskey",
    "MfaRecoveryCode",
    "MfaTotp",
    "MissingTranslation",
    "ModelGroup",
    "ModelVariant",
    "ModelVariantEnergyLabel",
    "ModelVariantEnergyRating",
    "ModuleEntitlement",
    "Notification",
    "NotificationEvent",
    "NumberRange",
    "OutboxMessage",
    "PlateLookupCacheEntry",
    "ProcessedEvent",
    "ProviderCodeMap",
    "ProviderEntityRef",
    "PushSubscription",
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
    "StandardText",
    "StandardTextDelegation",
    "StockItem",
    "StockItemBooking",
    "StockItemLedger",
    "StockItemMedia",
    "StockItemOption",
    "StockItemPublishing",
    "StockItemWebsiteListing",
    "PublicStockListing",
    "StockNumberSequence",
    "TypeApproval",
    "TyreSpecCache",
    "UiTranslation",
    "UiTranslationRevision",
    "User",
    "UserConnectedAccount",
    "UserLocation",
    "UserNotificationPreference",
    "UserPreference",
    "Valuation",
    "ValuationContractCarry",
    "ValuationDeduction",
    "ValuationNumberSequence",
    "ValuationProviderQuote",
    "VariantDetailState",
    "VariantOption",
    "VariantOptionEquipmentFeature",
    "VariantOptionProviderCode",
    "VariantPrice",
    "VariantProviderCode",
    "VariantTypeApproval",
    "VehicleAccessory",
    "VehicleConfiguration",
    "VehicleConfigurationOption",
    "VehicleConfigurationOptionFeature",
    "VehicleMdm",
    "VehicleMdmCustodyEvent",
    "VehicleNumberSequence",
    "VehicleOdometerReading",
    "VehicleParty",
    "VehiclePlate",
    "VehicleStockHolding",
]
