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
export type DealershipMembershipSummary = Schemas['DealershipMembershipSummary']
export type LoginResponse = Schemas['LoginResponse']

// Customer — core, contact channels, external IDs, vehicles
export type CustomerType = Schemas['CustomerType']
export type Language = Schemas['Language']
export type Salutation = Schemas['Salutation']
export type LegalForm = Schemas['LegalForm']
export type PreferredChannel = Schemas['PreferredChannel']
export type PhoneType = Schemas['PhoneType']
export type EmailType = Schemas['EmailType']
export type CustomerLifecycleStatus = Schemas['CustomerLifecycleStatus']
export type CustomerSource = Schemas['CustomerSource']
export type AddressType = Schemas['AddressType']
export type CustomerAddressCreate = Schemas['CustomerAddressCreate']
export type CustomerAddressUpdate = Schemas['CustomerAddressUpdate']
export type CustomerAddressRead = Schemas['CustomerAddressRead']
export type CustomerAddressPage = Schemas['CustomerAddressPage']
export type CustomerRead = Schemas['CustomerRead']
export type CustomerPage = Schemas['CustomerPage']
export type CustomerPhoneCreate = Schemas['CustomerPhoneCreate']
export type CustomerEmailCreate = Schemas['CustomerEmailCreate']
export type CustomerPhoneRead = Schemas['CustomerPhoneRead']
export type CustomerEmailRead = Schemas['CustomerEmailRead']
export type CustomerPhonePage = Schemas['CustomerPhonePage']
export type CustomerEmailPage = Schemas['CustomerEmailPage']
export type CustomerPhoneUpdate = Schemas['CustomerPhoneUpdate']
export type CustomerEmailUpdate = Schemas['CustomerEmailUpdate']
export type AuditEventRead = Schemas['AuditEventRead']
export type AuditEventPage = Schemas['AuditEventPage']
export type VehiclePartyRole = Schemas['VehiclePartyRole']
export type VehiclePartySummary = Schemas['VehiclePartySummary']
export type CustomerVehicleRead = Schemas['CustomerVehicleRead']
export type CustomerVehiclePage = Schemas['CustomerVehiclePage']
export type CustomerExternalIdRead = Schemas['CustomerExternalIdRead']
export type CustomerExternalIdPage = Schemas['CustomerExternalIdPage']
export type TransactionType = Schemas['TransactionType']
export type TransactionStatus = Schemas['TransactionStatus']
export type TransactionRead = Schemas['TransactionRead']
export type TransactionPage = Schemas['TransactionPage']

// WP-5 PR-8/PR-9 — master-data admin, reference-data admin
export type MappingGapRead = Schemas['MappingGapRead']
export type MappingGapPage = Schemas['MappingGapPage']
export type ReferenceValueRead = Schemas['ReferenceValueRead']
export type ReferenceValuePage = Schemas['ReferenceValuePage']
export type ReferenceValueCreate = Schemas['ReferenceValueCreate']
export type ReferenceValueUpdate = Schemas['ReferenceValueUpdate']

// VehicleMdm identity, plates, odometer, custody, provenance
export type CatalogueMatchStatus = Schemas['CatalogueMatchStatus']
export type OdometerSource = Schemas['OdometerSource']
export type VehicleMdmRead = Schemas['VehicleMdmRead']
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
export type StockItemGroupRead = Schemas['StockItemGroupRead']
export type StockItemGroupPage = Schemas['StockItemGroupPage']
export type StockItemPage = Schemas['StockItemPage']
export type LedgerCategory = Schemas['LedgerCategory']
export type LedgerDirection = Schemas['LedgerDirection']
export type LedgerEntryRead = Schemas['LedgerEntryRead']
export type LedgerEntryPage = Schemas['LedgerEntryPage']
export type MarketplaceChannel = Schemas['MarketplaceChannel']
export type PublishingState = Schemas['PublishingState']
export type BlockingCondition = Schemas['BlockingCondition']
export type PublishingRead = Schemas['PublishingRead']
export type MediaRead = Schemas['MediaRead']

// Sales — offer/contract container state, valuation
export type OfferContainerState = Schemas['OfferContainerState']
export type ValuationCreate = Schemas['ValuationCreate']

// Integration / dealership / catalogue sync
export type CapabilityCheckRead = Schemas['CapabilityCheckRead']
// The full entity (address, licensing, VAT rate...) — no longer a
// hand-trimmed projection. Existing call sites only ever read `.id` /
// `.legalName`, which are still there, so this is a strict widening.
export type DealershipRead = Schemas['DealershipRead']
export type DealershipPage = Schemas['DealershipPage']
export type CatalogueSyncStatusRead = Schemas['CatalogueSyncStatusRead']

// ============================================================================
// RENAMED — same backend schema, different name on the frontend. Python
// class names are scoped per module; OpenAPI's components.schemas is one
// flat namespace, and the frontend has historically added a context prefix
// (Sales/Integration/Stock) for its own readability that the backend's bare
// class name doesn't carry. Verified against the live schema, not guessed.
// ============================================================================

export type CustomerCreateInput = Schemas['CustomerCreate']
export type CustomerUpdateInput = Schemas['CustomerUpdate']

export type StockLifecycleStatus = Schemas['LifecycleStatus']
export type StockReservationState = Schemas['ReservationState']

export type SalesOfferStatus = Schemas['OfferStatus']
export type SalesFinancingKind = Schemas['FinancingKind']
export type SalesContractStatus = Schemas['ContractStatus']
export type SalesLineItemKind = Schemas['LineItemKind']
export type SalesDocumentOwnerType = Schemas['DocumentOwnerType']
export type SalesDocumentRead = Schemas['DocumentRead']
export type SalesDocumentPage = Schemas['DocumentPage']

export type ValuationSourceValue = Schemas['ValuationSource']
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

// A genuine backend naming COLLISION, not a frontend choice: two different
// Python classes are both named `VehicleStatus` — the legacy `vehicle`
// table's status (in_transit/in_stock/sold/in_service/totaled/scrapped,
// CLAUDE.md's "legacy vehicle table was never migrated off" gap) and
// vehicle-mdm's (active/exported/scrapped/stolen, which is what this frontend
// type has always meant). FastAPI disambiguates same-named schemas with a
// module-path-qualified key; there is no unqualified `VehicleStatus` in the
// published schema at all. Worth its own backend ticket — out of scope here
// ("do not change any backend schema").
export type VehicleStatus = Schemas['app__vehicle__models__vehicle_mdm__VehicleStatus']

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

export type SalesContractRead = Omit<Schemas['ContractRead'], 'vehicleSource'> & {
  vehicleSource: 'stock' | 'manual' | null
}
export type SalesContractPage = Omit<Schemas['ContractPage'], 'items'> & { items: SalesContractRead[] }

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
export type CustomerDuplicateCandidate = Omit<Schemas['CustomerDuplicateCandidate'], 'match'> & {
  match: DuplicateMatchKind
}
export type CustomerDuplicateCandidateList = Omit<Schemas['CustomerDuplicateCandidateList'], 'items'> & {
  items: CustomerDuplicateCandidate[]
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
