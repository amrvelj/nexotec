"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate). Lives
outside all ten context packages, like app.main and app.db — nothing but
this composition role.
"""

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJob, ServiceOrderPosition
from app.aftersales.models.service_package import ServicePackage, ServicePackagePosition
from app.contact.models.contact import (
    Contact,
    ContactAddress,
    ContactGroupAssignment,
    ContactRoleAssignment,
    ContactTag,
)
from app.contact.models.legal_basis import LegalBasis
from app.contact.models.stock_item_booking import StockItemBooking
from app.contact.models.vehicle_party import VehicleParty
from app.core.audit_model import AuditEvent
from app.core.daily_scheduler_model import DailyJobRun
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun
from app.integration.models.ai_prompt import IntegrationAiPrompt
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.caption import IntegrationProviderCaption, IntegrationProviderCaptionFetch
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.google_business_profile import (
    GbpDriftCheck,
    GbpLinkState,
    GbpLocationBinding,
    GbpPhotoJob,
    GbpPhotoUpload,
    GbpPushState,
)
from app.integration.models.mail import MailLinkState, MessageDelivery, SendingIdentity
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
from app.planning.models.booking import Booking, BookingResource, OverruledWarning
from app.planning.models.lists import Qualification, TechnicianType, WorkplaceType
from app.planning.models.resource import Resource, ResourceQualification, WorkingHours
from app.planning.models.starting_set import StartingSet
from app.platform.administration.models.export_job import ExportJob
from app.platform.administration.models.setup_checklist import SetupChecklistRegression
from app.platform.administration.models.status_page import PlatformStatusIncident, StatusItemState
from app.platform.documents.models.document_link import DocumentLink
from app.platform.documents.models.document_template import DocumentTemplate
from app.platform.documents.models.standard_text import StandardText
from app.platform.identity.models.auth_event import AuthEvent
from app.platform.identity.models.auth_session import AuthSession
from app.platform.identity.models.auth_token import AuthToken
from app.platform.identity.models.connected_account import UserConnectedAccount
from app.platform.identity.models.credential import Credential
from app.platform.identity.models.dealership_auth_config import DealershipAuthConfig
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.legal import LegalAcceptance, LegalDocument
from app.platform.identity.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.identity.models.notification import (
    Notification,
    NotificationEvent,
    PushSubscription,
    UserNotificationPreference,
)
from app.platform.identity.models.standard_text_delegation import StandardTextDelegation
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.models.user import User
from app.platform.identity.models.user_labour_category import UserLabourCategory
from app.platform.identity.models.user_location import UserLocation
from app.platform.identity.models.user_preference import UserPreference
from app.platform.organisation.models.branding_asset import BrandingAsset
from app.platform.organisation.models.contact_group import DealershipContactGroup
from app.platform.organisation.models.dealership import DealerGroup, Dealership, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.organisation.models.labour_rate import LabourRate, LabourRateAgreement, LabourRateCategory
from app.platform.organisation.models.location_schedule import LocationClosure, LocationOpeningHours
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRange, NumberRangeConfirmation
from app.platform.organisation.models.publishing_profile import LocationLeadRouting, PublishingProfile
from app.platform.organisation.models.setting_value import SettingValue, SettingValueChange
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.platform.reference.models.ui_translation import MissingTranslation, UiTranslation, UiTranslationRevision
from app.reporting.models.public_stock_listing import PublicStockListing
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
    "Booking",
    "BookingResource",
    "Brand",
    "BrandingAsset",
    "ColourCache",
    "Contact",
    "ContactAddress",
    "ContactGroupAssignment",
    "ContactRoleAssignment",
    "ContactTag",
    "Credential",
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
    "DocumentLink",
    "DocumentTemplate",
    "ExportJob",
    "GbpDriftCheck",
    "GbpLinkState",
    "GbpLocationBinding",
    "GbpPhotoJob",
    "GbpPhotoUpload",
    "GbpPushState",
    "Holiday",
    "HolidayCalendar",
    "IdempotencyRecord",
    "ImageRef",
    "IntegrationAiPrompt",
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
    "LabourRate",
    "LabourRateAgreement",
    "LabourRateCategory",
    "LegalAcceptance",
    "LegalBasis",
    "LegalDocument",
    "Location",
    "LocationClosure",
    "LocationLeadRouting",
    "LocationOpeningHours",
    "MailLinkState",
    "MappingGap",
    "MessageDelivery",
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
    "NumberRangeConfirmation",
    "OutboxMessage",
    "OverruledWarning",
    "PlateLookupCacheEntry",
    "PlatformStatusIncident",
    "ProcessedEvent",
    "ProviderCodeMap",
    "ProviderEntityRef",
    "PublicStockListing",
    "PublishingProfile",
    "PushSubscription",
    "Qualification",
    "ReconciliationOrphan",
    "ReconciliationRun",
    "ReferenceList",
    "ReferenceValue",
    "Resource",
    "ResourceQualification",
    "SalesContract",
    "SalesDeal",
    "SalesDocument",
    "SalesLineItem",
    "SalesNumberSequence",
    "SalesOffer",
    "SalesStockItemPurchase",
    "SendingIdentity",
    "ServiceOrder",
    "ServiceOrderJob",
    "ServiceOrderPosition",
    "ServicePackage",
    "ServicePackagePosition",
    "SettingValue",
    "SettingValueChange",
    "SetupChecklistRegression",
    "StandardText",
    "StandardTextDelegation",
    "StartingSet",
    "StatusItemState",
    "StockItem",
    "StockItemBooking",
    "StockItemLedger",
    "StockItemMedia",
    "StockItemOption",
    "StockItemPublishing",
    "StockItemWebsiteListing",
    "StockNumberSequence",
    "SupportSession",
    "TechnicianType",
    "TypeApproval",
    "TyreSpecCache",
    "UiTranslation",
    "UiTranslationRevision",
    "User",
    "UserConnectedAccount",
    "UserLabourCategory",
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
    "WorkingHours",
    "WorkplaceType",
]
