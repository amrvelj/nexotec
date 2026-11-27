// Generated from the backend's own OpenAPI schema (KAN-35) — see
// `make generate-frontend-types` / `npm run generate:api-types`. Do not
// hand-declare a type here that describes a response or request body; add
// it to the matching section below instead, and if the shape genuinely
// isn't in `schema.d.ts`, that's a sign the derivation is missing, not a
// reason to write it out by hand. `schema.d.ts` is regenerated from
// `app.openapi()` and diffed by CI (`frontend-openapi-drift`) — if a
// backend field changes without this file changing, that job goes red.
import type { components } from './schema'

type Schemas = components['schemas']

// ============================================================================
// GENERATED — one backend OpenAPI schema, same name on both sides.
// ============================================================================

// Auth / User
export type AccessRole = Schemas['AccessRole']
export type UserStatus = Schemas['UserStatus']
export type UserRead = Schemas['UserRead']
// KAN-200: the Users screen's read — the session's `UserRead` keeps its pre-KAN-200 shape.
export type UserAdminRead = Schemas['UserAdminRead']
// KAN-201: My account — the caller's own record and connected accounts.
export type MeRead = Schemas['MeRead']
export type MeUpdate = Schemas['MeUpdate']
export type ConnectedAccountRead = Schemas['ConnectedAccountRead']
export type ConnectedAccountListRead = Schemas['ConnectedAccountListRead']
// The manager's narrower read: provider, scopes, status (D-A-15).
export type ConnectedAccountManagerRead = Schemas['ConnectedAccountManagerRead']
export type ConnectedAccountManagerListRead = Schemas['ConnectedAccountManagerListRead']
// KAN-215: contact mail through the dealer's own mailbox.
export type MailProviderCode = Schemas['MailProviderCode']
export type MailboxRead = Schemas['MailboxRead']
export type BusinessMailboxRead = Schemas['BusinessMailboxRead']
export type OwnMailboxRead = Schemas['OwnMailboxRead']
export type LinkStartRead = Schemas['LinkStartRead']
export type LinkOutcomeRead = Schemas['LinkOutcomeRead']
// KAN-216: staff notifications — the inbox, the per-family preferences, Web Push.
export type NotificationRead = Schemas['NotificationRead']
export type NotificationPage = Schemas['NotificationPage']
export type UnreadCountRead = Schemas['UnreadCountRead']
export type NotificationFamily = Schemas['NotificationFamily']
export type NotificationTiming = Schemas['NotificationTiming']
export type NotificationPreferenceRead = Schemas['NotificationPreferenceRead']
export type NotificationPreferenceList = Schemas['NotificationPreferenceList']
export type NotificationPreferenceUpdate = Schemas['NotificationPreferenceUpdate']
export type PushConfigRead = Schemas['PushConfigRead']
export type PushSubscriptionCreate = Schemas['PushSubscriptionCreate']
export type PushSubscriptionRead = Schemas['PushSubscriptionRead']
// KAN-202: platform staff's account recovery (FR-AU-25…27).
export type RecoveryAction = Schemas['RecoveryAction']
export type RecoveryRequest = Schemas['RecoveryRequest']
export type GrantManagerRequest = Schemas['GrantManagerRequest']
export type ChangeEmailRequest = Schemas['ChangeEmailRequest']
export type RecoveryResult = Schemas['RecoveryResult']
export type RecoveryUserRead = Schemas['RecoveryUserRead']
export type RecoveryUserList = Schemas['RecoveryUserList']
// KAN-221 PR-6 — the support-session banner, the log, the Platform users panel
export type SupportSessionBanner = Schemas['SupportSessionBanner']
export type MySupportSessionRead = Schemas['MySupportSessionRead']
export type SupportSessionRead = Schemas['SupportSessionRead']
export type SupportSessionLogEntry = Schemas['SupportSessionLogEntry']
export type SupportSessionLogPage = Schemas['SupportSessionLogPage']
export type PlatformStaffEntry = Schemas['PlatformStaffEntry']
export type PlatformStaffPage = Schemas['PlatformStaffPage']
export type PlatformStaffCreate = Schemas['PlatformStaffCreate']
export type PlatformStaffRevoked = Schemas['PlatformStaffRevoked']
export type DealershipMembershipSummary = Schemas['DealershipMembershipSummary']
export type LoginResponse = Schemas['LoginResponse']
// KAN-196 — the identity front door and My account › Security
export type LoginRequest = Schemas['LoginRequest']
export type LoginStepResponse = Schemas['LoginStepResponse']
export type MfaVerifyRequest = Schemas['MfaVerifyRequest']
export type PasskeyOptionsResponse = Schemas['PasskeyOptionsResponse']
export type PasswordResetRequest = Schemas['PasswordResetRequest']
export type PasswordResetConfirm = Schemas['PasswordResetConfirm']
export type InvitationAccept = Schemas['InvitationAccept']
export type SessionRead = Schemas['SessionRead']
export type SessionList = Schemas['SessionList']
export type PasskeyRead = Schemas['PasskeyRead']
export type MfaStatusRead = Schemas['MfaStatusRead']
export type TotpEnrolmentRead = Schemas['TotpEnrolmentRead']
export type RecoveryCodesRead = Schemas['RecoveryCodesRead']
export type AuthConfigRead = Schemas['AuthConfigRead']
export type AuthConfigUpdate = Schemas['AuthConfigUpdate']
// KAN-210 — Platform Administration › Onboarding.
export type OnboardingRequest = Schemas['OnboardingRequest']
export type OnboardingResult = Schemas['OnboardingResult']
export type DealerGroupOptionList = Schemas['DealerGroupOptionList']
export type AuthEventRead = Schemas['AuthEventRead']
export type LoginStatusRead = Schemas['LoginStatusRead']
// KAN-200 — the Users screen: fields, locations, the deactivation step
export type UserRole = Schemas['UserRole']
export type EmploymentStatus = Schemas['EmploymentStatus']
export type UserCreate = Schemas['UserCreate']
export type UserUpdate = Schemas['UserUpdate']
export type UserPage = Schemas['UserPage']
export type LocationRead = Schemas['LocationRead']
export type LocationList = Schemas['LocationList']
// KAN-204 — Locations: master data, opening hours, closures, holiday calendars
export type LocationType = Schemas['LocationType']
export type LocationCreate = Schemas['LocationCreate']
export type LocationUpdate = Schemas['LocationUpdate']
export type LocationDepartment = Schemas['LocationDepartment']
export type OpeningHoursDay = Schemas['OpeningHoursDay']
export type OpeningHoursUpdate = Schemas['OpeningHoursUpdate']
export type OpeningHoursRow = Schemas['OpeningHoursRow']
export type OpeningHoursRead = Schemas['OpeningHoursRead']
export type ClosureCreate = Schemas['ClosureCreate']
export type ClosureUpdate = Schemas['ClosureUpdate']
export type ClosureRead = Schemas['ClosureRead']
export type ClosureList = Schemas['ClosureList']
export type HolidayRead = Schemas['HolidayRead']
export type HolidayCalendarRead = Schemas['HolidayCalendarRead']
export type HolidayCalendarList = Schemas['HolidayCalendarList']
export type HolidayDateRead = Schemas['HolidayDateRead']
export type HolidayDateList = Schemas['HolidayDateList']
export type OwnedGroupRead = Schemas['OwnedGroupRead']
export type OwnershipRead = Schemas['OwnershipRead']
export type OwnershipTransferRequest = Schemas['OwnershipTransferRequest']
export type OwnershipTransferResult = Schemas['OwnershipTransferResult']
export type OwnershipTransfersRequest = Schemas['OwnershipTransfersRequest']
export type OwnershipTransfersRead = Schemas['OwnershipTransfersRead']
export type UserDeactivateRequest = Schemas['UserDeactivateRequest']
export type UserDeactivatedRead = Schemas['UserDeactivatedRead']
export type StandardTextCategory = Schemas['StandardTextCategory']
export type TextDelegationsRead = Schemas['TextDelegationsRead']
export type MembershipRead = Schemas['MembershipRead']
export type MembershipList = Schemas['MembershipList']
export type TextDelegationsUpdate = Schemas['TextDelegationsUpdate']
// KAN-208: the standard-text library (Dealer Administration › Standard texts).
export type StandardTextAppliesTo = Schemas['StandardTextAppliesTo']
export type StandardTextSlot = Schemas['StandardTextSlot']
export type StandardTextRead = Schemas['StandardTextRead']
export type StandardTextList = Schemas['StandardTextList']
// KAN-207 — number ranges.
export type NumberRangeRead = Schemas['NumberRangeRead']
export type NumberRangeList = Schemas['NumberRangeList']
export type NumberRangeDocumentType = Schemas['NumberRangeRead']['documentType']
export type NumberRangePreviewRead = Schemas['NumberRangePreviewRead']
// KAN-223 — the Betrieb's status page and Nexotec's incidents.
export type StatusPageRead = Schemas['StatusPageRead']
export type StatusItemRead = Schemas['StatusItemRead']
export type StatusState = Schemas['StatusItemRead']['state']
export type StatusIncidentRead = Schemas['StatusIncidentRead']
export type StatusIncidentList = Schemas['StatusIncidentList']
export type StatusIncidentCreate = Schemas['StatusIncidentCreate']
export type StatusIncidentUpdate = Schemas['StatusIncidentUpdate']
export type IncidentSeverity = Schemas['StatusIncidentRead']['severity']
// KAN-776 — Nexotec's Business Profile client on the platform status view.
export type BusinessProfileClientStatusRead = Schemas['BusinessProfileClientStatusRead']
export type BusinessProfileClientCheckRead = Schemas['BusinessProfileClientCheckRead']
// KAN-226 PR-4 — a location's Business Profile binding, its drift and the link flow.
export type BusinessProfileBindingRead = Schemas['BusinessProfileBindingRead']
export type BusinessProfileBindingPage = Schemas['BusinessProfileBindingPage']
export type BusinessProfileDriftRead = Schemas['BusinessProfileDriftRead']
export type BusinessProfileLinkStartRead = Schemas['BusinessProfileLinkStartRead']
export type BusinessProfileLinkOutcomeRead = Schemas['BusinessProfileLinkOutcomeRead']
export type BusinessProfileGoogleChoicePage = Schemas['BusinessProfileGoogleChoicePage']
export type BusinessProfileGoogleChoiceRead = Schemas['BusinessProfileGoogleChoiceRead']
// KAN-214 — setup checklist, activation and the legal documents.
export type SetupChecklistRead = Schemas['SetupChecklistRead']
export type SetupChecklistItemRead = Schemas['SetupChecklistItemRead']
export type ActivationRead = Schemas['ActivationRead']
export type NumberRangeConfirmationRead = Schemas['NumberRangeConfirmationRead']
export type LegalDocumentSummary = Schemas['LegalDocumentSummary']
export type LegalDocumentRead = Schemas['LegalDocumentRead']
export type LegalDocumentType = Schemas['LegalDocumentRead']['documentType']
export type LegalDocumentList = Schemas['LegalDocumentList']
export type LegalDocumentPublish = Schemas['LegalDocumentPublish']
export type CurrentLegalDocuments = Schemas['CurrentLegalDocuments']
export type LegalAcceptanceDue = Schemas['LegalAcceptanceDue']
export type LegalAcceptanceRead = Schemas['LegalAcceptanceRead']
// KAN-220 — copy configuration from a sister Betrieb.
export type ConfigurationCopyRequest = Schemas['ConfigurationCopyRequest']
export type ConfigurationCopyResult = Schemas['ConfigurationCopyResult']
export type ConfigurationCopyItem = Schemas['ConfigurationCopyItem']
export type ConfigurationCopySourceList = Schemas['ConfigurationCopySourceList']
export type ConfigurationCopySubset = Schemas['ConfigurationCopyItem']['subset']
// KAN-466 — contact groups per Betrieb.
export type ContactGroupRead = Schemas['ContactGroupRead']
export type ContactGroupList = Schemas['ContactGroupList']
// KAN-805 — the settings store (Dealer Administration › Einstellungen).
export type SettingAreaList = Schemas['SettingAreaList']
export type AreaSettingsRead = Schemas['AreaSettingsRead']
export type AreaSettingRead = Schemas['AreaSettingRead']
export type SettingDeclarationRead = Schemas['SettingDeclarationRead']
export type SettingSlotRead = Schemas['SettingSlotRead']
export type SettingLocationRead = Schemas['SettingLocationRead']
export type SettingValueBody = Schemas['SettingValueBody']
export type SettingChangeRead = Schemas['SettingChangeRead']
export type SettingChangePage = Schemas['SettingChangePage']
// KAN-218 — the data export and offboarding.
export type ExportRead = Schemas['ExportRead']
export type ExportPage = Schemas['ExportPage']
export type ExportJobKind = Schemas['ExportJobKind']
export type ExportJobStatus = Schemas['ExportJobStatus']
export type ArchiveExportRequest = Schemas['ArchiveExportRequest']
export type OffboardRequest = Schemas['OffboardRequest']
export type OffboardingRead = Schemas['OffboardingRead']
export type StandardTextCreate = Schemas['StandardTextCreate']
export type StandardTextUpdate = Schemas['StandardTextUpdate']
export type StandardTextPreviewRequest = Schemas['StandardTextPreviewRequest']
export type StandardTextPreviewRead = Schemas['StandardTextPreviewRead']
export type StandardTextRegistryRead = Schemas['StandardTextRegistryRead']
export type StandardTextTargetRead = Schemas['StandardTextTargetRead']
// KAN-209: the seed library in Platform Administration › Translations.
export type StandardTextSeedRead = Schemas['StandardTextSeedRead']
export type StandardTextSeedGroup = Schemas['StandardTextSeedGroup']
export type StandardTextSeedList = Schemas['StandardTextSeedList']
export type StandardTextSeedUpdate = Schemas['StandardTextSeedUpdate']

// Contact — core, contact channels, external IDs, vehicles
export type ContactType = Schemas['ContactType']
export type Language = Schemas['Language']
export type Salutation = Schemas['Salutation']
export type LegalForm = Schemas['LegalForm']
export type PreferredChannel = Schemas['PreferredChannel']
export type PhoneType = Schemas['PhoneType']
export type EmailType = Schemas['EmailType']
export type ConsentScope = Schemas['ConsentScope']
export type ConsentSource = Schemas['ConsentSource']
export type ContactLifecycleStatus = Schemas['ContactLifecycleStatus']
export type ContactRole = Schemas['ContactRole']
export type ContactSource = Schemas['ContactSource']
export type AddressType = Schemas['AddressType']
// FR-17 / FR-18 stored fields (KAN-50)
export type Gender = Schemas['Gender']
export type PaymentTerms = Schemas['PaymentTerms']
export type ContactAdvisorOption = Schemas['ContactAdvisorOption']
export type ContactAdvisorOptionList = Schemas['ContactAdvisorOptionList']
export type ContactAddressCreate = Schemas['ContactAddressCreate']
export type ContactAddressUpdate = Schemas['ContactAddressUpdate']
export type ContactAddressRead = Schemas['ContactAddressRead']
export type ContactAddressPage = Schemas['ContactAddressPage']
export type ContactRead = Schemas['ContactRead']
export type ContactPage = Schemas['ContactPage']
export type ContactPhoneCreate = Schemas['ContactPhoneCreate']
export type ContactEmailCreate = Schemas['ContactEmailCreate']
export type ContactPhoneRead = Schemas['ContactPhoneRead']
export type ContactEmailRead = Schemas['ContactEmailRead']
export type ContactPhonePage = Schemas['ContactPhonePage']
export type ContactEmailPage = Schemas['ContactEmailPage']
export type ContactPhoneUpdate = Schemas['ContactPhoneUpdate']
export type ContactEmailUpdate = Schemas['ContactEmailUpdate']
export type AuditEventRead = Schemas['AuditEventRead']
export type AuditEventPage = Schemas['AuditEventPage']
export type ContactHistoryPage = Schemas['ContactHistoryPage']
export type VehiclePartyRole = Schemas['VehiclePartyRole']
export type VehiclePartySummary = Schemas['VehiclePartySummary']
export type OtherVehiclePartySummary = Schemas['OtherVehiclePartySummary']
export type VehicleStockLinkSummary = Schemas['VehicleStockLinkSummary']
export type ContactVehicleRead = Schemas['ContactVehicleRead']
export type ContactVehiclePage = Schemas['ContactVehiclePage']
export type ContactExternalIdRead = Schemas['ContactExternalIdRead']
export type ContactExternalIdPage = Schemas['ContactExternalIdPage']

// WP-5 PR-8/PR-9 — master-data admin, reference-data admin
export type MappingGapRead = Schemas['MappingGapRead']
export type MappingGapPage = Schemas['MappingGapPage']
export type ReferenceValueRead = Schemas['ReferenceValueRead']

// KAN-209: UI translations — the runtime map, the Translations panel, the missing-key log.
export type UiTranslationMap = Schemas['UiTranslationMap']
export type UiTranslationRead = Schemas['UiTranslationRead']
export type UiTranslationKeyRead = Schemas['UiTranslationKeyRead']
export type UiTranslationKeyPage = Schemas['UiTranslationKeyPage']
export type UiTranslationNamespaces = Schemas['UiTranslationNamespaces']
export type UiTranslationPublish = Schemas['UiTranslationPublish']
export type MissingTranslationRead = Schemas['MissingTranslationRead']
export type MissingTranslationPage = Schemas['MissingTranslationPage']
export type ReferenceValuePage = Schemas['ReferenceValuePage']
export type ReferenceValueCreate = Schemas['ReferenceValueCreate']
export type ReferenceValueUpdate = Schemas['ReferenceValueUpdate']
export type BrandRead = Schemas['BrandRead']
export type BrandPage = Schemas['BrandPage']
export type CaptionFetchList = Schemas['CaptionFetchList']
export type CaptionFetchRead = Schemas['CaptionFetchRead']

// Configurator C-B (KAN-40) — catalogue browse & facet search (FR-C-01)
export type CatalogueBrowseMode = Schemas['CatalogueBrowseMode']
export type CatalogueBrowseState = Schemas['CatalogueBrowseState']
// KAN-767 — which query a `notInContract` answer refused (and the option, when known)
export type CatalogueBrowseOperation = Schemas['CatalogueBrowseOperation']
export type CatalogueRefusalRead = Schemas['CatalogueRefusalRead']
export type CatalogueBrandRead = Schemas['CatalogueBrandRead']
export type CatalogueBrandPage = Schemas['CatalogueBrandPage']
export type VehicleSpecBlockRead = Schemas['VehicleSpecBlockRead']
export type CatalogueVariantRead = Schemas['CatalogueVariantRead']
export type CatalogueVariantPage = Schemas['CatalogueVariantPage']
export type CatalogueVariantPrice = Schemas['CatalogueVariantPrice']
export type CatalogueModelGroupRead = Schemas['CatalogueModelGroupRead']
export type CatalogueModelGroupPage = Schemas['CatalogueModelGroupPage']
export type CatalogueFacetsRead = Schemas['CatalogueFacetsRead']
export type CatalogueFacetValue = Schemas['CatalogueFacetValue']

// Configurator C-E (KAN-43) — the catalogue mirror's own options/colours/
// tyres/images read, entitlement-degraded (shared with the vehicle-mdm
// screen's identical endpoint)
export type CatalogueSpecificationRead = Schemas['CatalogueSpecificationRead']
export type CatalogueOptionRead = Schemas['CatalogueOptionRead']
export type CatalogueColourRead = Schemas['CatalogueColourRead']
export type CatalogueTyreSpecRead = Schemas['CatalogueTyreSpecRead']
export type CatalogueImageRead = Schemas['CatalogueImageRead']
export type CatalogueOptionRelationRead = Schemas['CatalogueOptionRelationRead']

// Configurator C-C (KAN-41) — the configuration entity
export type ConfigurationSource = Schemas['ConfigurationSource']
export type ConfigurationMode = Schemas['ConfigurationMode']
export type ConfigurationMatchStatus = Schemas['ConfigurationMatchStatus']
export type ConfigurationMatchMethod = Schemas['ConfigurationMatchMethod']
export type ConfigurationRead = Schemas['ConfigurationRead']
export type ConfigurationOptionRead = Schemas['ConfigurationOptionRead']
export type ConfigurationOptionInput = Schemas['ConfigurationOptionInput']
export type ConfigurationOptionsReplace = Schemas['ConfigurationOptionsReplace']
export type ConfigurationCreate = Schemas['ConfigurationCreate']
export type ConfigurationUpdate = Schemas['ConfigurationUpdate']
// C-D (KAN-42) — the FR-C-02 identification waterfall
export type IdentificationRead = Schemas['IdentificationRead']
export type IdentificationOutcome = Schemas['IdentificationOutcome']
export type IdentificationNote = Schemas['IdentificationNote']
export type IdentifierKind = Schemas['IdentifierKind']
export type VariantCandidateRead = Schemas['VariantCandidateRead']
export type PlateRecordRead = Schemas['PlateRecordRead']
export type ObservedIdentityRead = Schemas['ObservedIdentityRead']
export type BestMatchProposalRead = Schemas['BestMatchProposalRead']
export type NewPriceSource = Schemas['NewPriceSource']
// C-F (KAN-10) — FR-C-16 re-sync
export type ResyncFieldRead = Schemas['ResyncFieldRead']
export type ResyncRequest = Schemas['ResyncRequest']

// VehicleMdm identity, plates, odometer, custody, provenance
export type CatalogueMatchStatus = Schemas['CatalogueMatchStatus']
export type OdometerSource = Schemas['OdometerSource']
export type VehicleMdmRead = Schemas['VehicleMdmRead']
// KAN-667: the Vehicle 360's read of one record and the session's own Betrieb's stock car holding it.
export type VehicleMdmDetailRead = Schemas['VehicleMdmDetailRead']
export type VehicleStockItemLink = Schemas['VehicleStockItemLink']
export type VehicleMdmCreateResult = Schemas['VehicleMdmCreateResult']
export type VehicleMdmPage = Schemas['VehicleMdmPage']
export type VehiclePickerCandidate = Schemas['VehiclePickerCandidate']
export type VehicleSearchResult = Schemas['VehicleSearchResult']
export type VehiclePartyAllocationRead = Schemas['VehiclePartyAllocationRead']
export type VehiclePlateRead = Schemas['VehiclePlateRead']
export type VehicleOdometerReadingRead = Schemas['VehicleOdometerReadingRead']
export type VehicleAccessoryRead = Schemas['VehicleAccessoryRead']

// Stock / inventory
export type StockItemCondition = Schemas['StockItemCondition']
export type AgeingBucket = Schemas['AgeingBucket']
export type StockItemRead = Schemas['StockItemRead']
export type StockItemCreateRequest = Schemas['StockItemCreateRequest']
export type StockItemConfigurationAttach = Schemas['StockItemConfigurationAttach']
export type StockItemGroupRead = Schemas['StockItemGroupRead']
export type StockItemGroupPage = Schemas['StockItemGroupPage']
// KAN-565: the group stock grid's «Betrieb» filter options.
export type GroupStockDealershipRead = Schemas['GroupStockDealershipRead']
export type GroupStockDealershipList = Schemas['GroupStockDealershipList']
export type StockItemPage = Schemas['StockItemPage']
// KAN-461: a list endpoint's served column registry (app.core.list_query).
export type GridSchemaRead = Schemas['GridSchemaRead']
export type GridField = Schemas['GridField']
export type LedgerCategory = Schemas['LedgerCategory']
export type LedgerDirection = Schemas['LedgerDirection']
export type LedgerEntryRead = Schemas['LedgerEntryRead']
export type LedgerEntryPage = Schemas['LedgerEntryPage']
export type MarketplaceChannel = Schemas['MarketplaceChannel']
export type PublishingState = Schemas['PublishingState']
export type TransmissionStatus = Schemas['TransmissionStatus']
export type BlockingCondition = Schemas['BlockingCondition']
export type PublishingRead = Schemas['PublishingRead']
export type MediaRead = Schemas['MediaRead']
export type EquipmentRead = Schemas['EquipmentRead']

// Sales — offer/contract container state, valuation
export type OfferContainerState = Schemas['OfferContainerState']
export type ValuationCreate = Schemas['ValuationCreate']
// KAN-552 — the identAPI's valuation of a configured car, stored before the valuation is created.
export type ProviderQuoteRead = Schemas['ProviderQuoteRead']
export type ProviderQuoteRequest = Schemas['ProviderQuoteRequest']
export type ResidualValueRead = Schemas['ResidualValueRead']

// Integration / dealership / catalogue sync
export type CapabilityCheckRead = Schemas['CapabilityCheckRead']
// The full entity (address, licensing, VAT rate...) — no longer a
// hand-trimmed projection. Existing call sites only ever read `.id` /
// `.legalName`, which are still there, so this is a strict widening.
export type DealershipRead = Schemas['DealershipRead']
export type DealershipPage = Schemas['DealershipPage']
// KAN-203 — Dealer Administration › Business: the master data PATCH, the
// bank-account collection (D-A-27) and the commercial and fiscal defaults.
export type DealershipUpdate = Schemas['DealershipUpdate']
export type DealershipAddress = Schemas['DealershipAddress']
export type DealershipOverrideAddress = Schemas['DealershipOverrideAddress']
export type DealershipCollectiveVehicleLicence = Schemas['DealershipCollectiveVehicleLicence']
export type FranchiseType = Schemas['FranchiseType']
export type BankAccountRead = Schemas['BankAccountRead']
export type BankAccountList = Schemas['BankAccountList']
export type BankAccountCreate = Schemas['BankAccountCreate']
export type BankAccountUpdate = Schemas['BankAccountUpdate']
export type QrReferenceType = Schemas['QrReferenceType']
export type DealershipSettingsRead = Schemas['DealershipSettingsRead']
export type DealershipSettingsUpdate = Schemas['DealershipSettingsUpdate']
export type AddressAutocompleteConfigRead = Schemas['AddressAutocompleteConfigRead']
export type ChartOfAccountsVariant = Schemas['ChartOfAccountsVariant']
export type PeriodLockPolicy = Schemas['PeriodLockPolicy']
// KAN-205 — Dealer Administration › Branding.
export type BrandingRead = Schemas['BrandingRead']
export type BrandingUpdate = Schemas['BrandingUpdate']
export type BrandingAssetRead = Schemas['BrandingAssetRead']
// KAN-225 — Dealer Administration › Publishing, and the Betrieb's website.
export type PublishingProfileRead = Schemas['PublishingProfileRead']
export type PublishingProfileUpdate = Schemas['PublishingProfileUpdate']
export type LocationOverrideRead = Schemas['LocationOverrideRead']
export type LocationOverrideUpdate = Schemas['LocationOverrideUpdate']
export type LeadRoutingRead = Schemas['LeadRoutingRead']
export type LeadRoutingUpdate = Schemas['LeadRoutingUpdate']
export type PublishingImageRead = Schemas['PublishingImageRead']
export type PublishingImageKind = Schemas['PublishingImageKind']
export type ProfileValidationRead = Schemas['ProfileValidationRead']
export type MissingItemRead = Schemas['MissingItemRead']
export type PublicStockRead = Schemas['PublicStockRead']
export type WebsiteListingRead = Schemas['WebsiteListingRead']
export type BrandingAssetKind = Schemas['LogoKind']
export type BrandingPlacements = Schemas['BrandingPlacements']
export type ContrastPairRead = Schemas['ContrastPairRead']
// KAN-206 — Dealer Administration › Letterhead and template.
export type DocumentTemplateRead = Schemas['DocumentTemplateRead']
export type DocumentTemplateUpdate = Schemas['DocumentTemplateUpdate']
export type HeaderLayout = Schemas['HeaderLayout']
export type LogoSize = Schemas['LogoSize']
export type AddressWindow = Schemas['AddressWindow']
export type FooterColumn = Schemas['FooterColumn']
export type LocationLogoRead = Schemas['LocationLogoRead']

// ============================================================================
// RENAMED — same backend schema, different name on the frontend. Python
// class names are scoped per module; OpenAPI's components.schemas is one
// flat namespace, and the frontend has historically added a context prefix
// (Sales/Integration/Stock) for its own readability that the backend's bare
// class name doesn't carry. Verified against the live schema, not guessed.
// ============================================================================

export type ContactCreateInput = Schemas['ContactCreate']
export type ContactUpdateInput = Schemas['ContactUpdate']

export type StockLifecycleStatus = Schemas['LifecycleStatus']
export type StockReservationState = Schemas['ReservationState']

export type SalesOfferStatus = Schemas['OfferStatus']
export type SalesFinancingKind = Schemas['FinancingKind']
export type SalesContractStatus = Schemas['ContractStatus']
export type SalesLineItemKind = Schemas['LineItemKind']
export type SalesDocumentOwnerType = Schemas['DocumentOwnerType']
export type SalesDocumentRead = Schemas['DocumentRead']
export type SalesDocumentPage = Schemas['DocumentPage']
export type SalesDocumentDeliveryRead = Schemas['DocumentDeliveryRead']
export type SalesSendOptions = Schemas['SendOptionsRead']
export type SalesSendChannelOption = Schemas['SendChannelOptionRead']
export type SalesMessageDeliveryRead = Schemas['MessageDeliveryRead']

export type ValuationSourceValue = Schemas['ValuationSource']
export type StockValuationRefRead = Schemas['ValuationRefRead']
export type ValuationDeductionInput = Schemas['DeductionInput']
export type ValuationDeductionRead = Schemas['DeductionRead']

export type IntegrationProviderRead = Schemas['ProviderRead']
export type IntegrationProviderPage = Schemas['ProviderPage']
export type ConnectionScopeValue = Schemas['ConnectionScope']
export type ConnectionEnvironmentValue = Schemas['ConnectionEnvironment']
export type ConnectionStatusValue = Schemas['ConnectionStatus']
export type IntegrationEntitlementRead = Schemas['EntitlementRead']
export type IntegrationSecretSlotRead = Schemas['SecretSlotRead']
export type IntegrationConnectionRead = Schemas['ConnectionRead']
export type IntegrationConnectionPage = Schemas['ConnectionPage']
export type IntegrationUsageRead = Schemas['UsageRead']

// vehicle-mdm's status (active/exported/scrapped/stolen). Until KAN-79 retired
// the legacy `vehicle` table its own VehicleStatus collided with this one and
// FastAPI published a module-qualified key instead.
export type VehicleStatus = Schemas['VehicleStatus']

// ============================================================================
// NARROWED — the backend field is a computed Python property typed `str`
// (not an enum.Enum column), so the published schema widens it to a plain
// string. These overrides restore the literal union every consumer already
// relies on. NOT covered by the CI drift guard: if the backend's actual set
// of values ever changes, nothing here goes red — verify by hand if the
// owning read model's shape changes.
//
// Every *Page/*List wrapper below is here for the same reason, one level
// removed: `schema.d.ts`'s own `OfferPage.items` etc. reference the RAW
// generated Read type, not the narrowed one two lines above — narrowing a
// type doesn't retroactively narrow every place that embeds it. Overriding
// `items` here once means every list-fetching call site gets the narrowed
// element type for free, instead of an `as` cast repeated at each call site.
// ============================================================================

export type SalesOfferRead = Omit<Schemas['OfferRead'], 'vehicleSource' | 'discountType'> & {
  vehicleSource: 'stock' | 'manual' | null
  discountType: 'percent' | 'amount' | null
}
export type SalesOfferPage = Omit<Schemas['OfferPage'], 'items'> & { items: SalesOfferRead[] }

export type SalesContractRead = Omit<Schemas['ContractRead'], 'vehicleSource' | 'discountType'> & {
  vehicleSource: 'stock' | 'manual' | null
  discountType: 'percent' | 'amount' | null
}
export type SalesContractPage = Omit<Schemas['ContractPage'], 'items'> & { items: SalesContractRead[] }

// KAN-67 (ADR-083) — an offer draft and a pending contract are edited by
// the same containers (modules/sales/components/deal-workspace/); either read model
// carries every field they touch.
export type SalesDealOwnerType = 'offer' | 'contract'
export type SalesPricedDealRead = SalesOfferRead | SalesContractRead

export type SalesLineItemRead = Omit<Schemas['LineItemRead'], 'discountType'> & {
  discountType: 'percent' | 'amount' | null
}
export type SalesLineItemPage = Omit<Schemas['LineItemPage'], 'items'> & { items: SalesLineItemRead[] }

// The overview grid's own read shape (ADR-060) — a deliberately separate
// schema from SalesOfferRead/SalesContractRead, mirroring
// app.inventory's StockItemGroupRead convention for a read model.
export type SalesDealRead = Omit<Schemas['DealRead'], 'entityType'> & {
  entityType: 'offer' | 'contract'
}
export type SalesDealPage = Omit<Schemas['DealPage'], 'items'> & { items: SalesDealRead[] }

export type ValuationStatusValue = 'draft' | 'valid' | 'expired' | 'used'
export type ValuationRead = Omit<Schemas['ValuationRead'], 'status'> & {
  status: ValuationStatusValue
}
export type ValuationPage = Omit<Schemas['ValuationPage'], 'items'> & { items: ValuationRead[] }

export type DuplicateMatchKind = 'exact' | 'similar'
export type ContactDuplicateCandidate = Omit<Schemas['ContactDuplicateCandidate'], 'match'> & {
  match: DuplicateMatchKind
}
export type ContactDuplicateCandidateList = Omit<Schemas['ContactDuplicateCandidateList'], 'items'> & {
  items: ContactDuplicateCandidate[]
}

// ============================================================================
// FRONTEND-ONLY — no backend OpenAPI schema exists to derive these from.
// ============================================================================

// Mirrors app/core/errors.py's error taxonomy (400/401/403/404/409/422, one
// body shape). register_error_handlers responds via exception handlers, not
// a declared response_model, so FastAPI never registers this in
// components.schemas — there is nothing to generate. Keep this in sync with
// app/core/errors.py by hand.
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: Record<string, unknown> | null
  }
}

// KAN-227 — the AI prompt library, the AI connection in use, «Text vorschlagen».
export type AiPromptRead = Schemas['AiPromptRead']
export type AiPromptList = Schemas['AiPromptList']
export type AiPromptCreate = Schemas['AiPromptCreate']
export type AiPromptUpdate = Schemas['AiPromptUpdate']
export type AiConnectionInUseRead = Schemas['AiConnectionInUseRead']
export type AiPromptSeedRead = Schemas['AiPromptSeedRead']
export type AiPromptSeedGroup = Schemas['AiPromptSeedGroup']
export type AiPromptSeedList = Schemas['AiPromptSeedList']
export type ListingTextSuggestionRead = Schemas['ListingTextSuggestionRead']
// KAN-806 — Dealer Administration › Labour rates (D-A-19)
export type LabourRateCategoryList = Schemas['LabourRateCategoryList']
export type LabourRateCategoryRead = Schemas['LabourRateCategoryRead']
// KAN-818 — the service catalogue (Aftersales' packages, S1 in Dealer Administration).
export type ServicePackageList = Schemas['ServicePackageList']
export type ServicePackageRead = Schemas['ServicePackageRead']
export type ServicePackageWrite = Schemas['ServicePackageWrite']
export type PackagePositionRead = Schemas['PackagePositionRead']
export type PackagePositionWrite = Schemas['PackagePositionWrite']
export type LabourRateRead = Schemas['LabourRateRead']
export type LabourRateAgreementPage = Schemas['LabourRateAgreementPage']
export type LabourRateAgreementRead = Schemas['LabourRateAgreementRead']
export type LabourCategoryCandidateList = Schemas['LabourCategoryCandidateList']
// KAN-808 — Dealer Administration › Planner resources (S7)
export type WorkplaceTypeList = Schemas['WorkplaceTypeList']
export type WorkplaceTypeRead = Schemas['WorkplaceTypeRead']
export type WorkplaceTypeCreate = Schemas['WorkplaceTypeCreate']
export type WorkplaceTypeUpdate = Schemas['WorkplaceTypeUpdate']
export type TechnicianTypeList = Schemas['TechnicianTypeList']
export type TechnicianTypeRead = Schemas['TechnicianTypeRead']
export type TechnicianTypeCreate = Schemas['TechnicianTypeCreate']
export type TechnicianTypeUpdate = Schemas['TechnicianTypeUpdate']
export type QualificationList = Schemas['QualificationList']
export type QualificationRead = Schemas['QualificationRead']
export type QualificationCreate = Schemas['QualificationCreate']
export type QualificationUpdate = Schemas['QualificationUpdate']
export type ResourceList = Schemas['ResourceList']
export type ResourceRead = Schemas['ResourceRead']
export type ResourceKind = Schemas['ResourceKind']
export type PersonCreate = Schemas['PersonCreate']
export type WorkplaceCreate = Schemas['WorkplaceCreate']
export type ResourceUpdate = Schemas['ResourceUpdate']
export type WorkingDay = Schemas['WorkingDay']
export type WorkingHoursUpdate = Schemas['WorkingHoursUpdate']
export type PersonCandidateList = Schemas['PersonCandidateList']
export type PersonCandidateRead = Schemas['PersonCandidateRead']
// KAN-809 — the planner page (PL1, PL2, PL4)
export type PlannerDayRead = Schemas['PlannerDayRead']
export type BookingRead = Schemas['BookingRead']
export type BookingOutcomeRead = Schemas['BookingOutcomeRead']
export type BookingMove = Schemas['BookingMove']
export type BlockCreate = Schemas['BlockCreate']
export type ConfirmedWarningWrite = Schemas['ConfirmedWarningWrite']
export type WarningCode = Schemas['WarningCode']
// KAN-810 — Aftersales: the workshop order (D1 list, D2 new order, D3 the order, D13 position, D16 cancel)
export type ServiceOrderState = Schemas['ServiceOrderState']
export type ServiceOrderSummary = Schemas['ServiceOrderSummary']
export type ServiceOrderRead = Schemas['ServiceOrderRead']
export type ServiceOrderPage = Schemas['ServiceOrderPage']
export type ServiceOrderCreate = Schemas['ServiceOrderCreate']
export type ServiceOrderPartiesUpdate = Schemas['ServiceOrderPartiesUpdate']
export type ServiceOrderCancel = Schemas['ServiceOrderCancel']
export type ServiceOrderJobRead = Schemas['JobRead']
export type ServiceOrderJobCreate = Schemas['JobCreate']
export type ServiceOrderPositionRead = Schemas['PositionRead']
export type ServiceOrderPositionCreate = Schemas['PositionCreate']
export type ServiceOrderPositionUpdate = Schemas['PositionUpdate']
export type ServiceOrderPayerTotalRead = Schemas['PayerTotalRead']
export type ServiceOrderTotalsRead = Schemas['OrderTotalsRead']
export type ServiceOrderHistoryRead = Schemas['HistoryRead']
export type ServiceOrderHistoryEntryRead = Schemas['HistoryEntryRead']
export type LabourRateProposalRead = Schemas['LabourRateProposalRead']
export type PositionKind = Schemas['PositionKind']
export type JobAnswerState = Schemas['JobAnswerState']
// KAN-811 — D8 the customer's answer per job
export type AnswerChannel = Schemas['AnswerChannel']
export type AnswerResult = Schemas['AnswerResult']
export type ServiceOrderAnswersCreate = Schemas['ServiceOrderAnswersCreate']
export type ServiceOrderJobAnswerRead = Schemas['JobAnswerRead']
// KAN-813 — the stay of the car, D14 «Fahrzeug ist da», the workshop status
export type ArrivalCreate = Schemas['ArrivalCreate']
export type StayRead = Schemas['StayRead']
export type WorkshopStatus = Schemas['WorkshopStatus']
export type LabourTimeUnit = Schemas['LabourTimeUnit']
export type RateSource = Schemas['RateSource']
export type PriceEntry = Schemas['PriceEntry']
