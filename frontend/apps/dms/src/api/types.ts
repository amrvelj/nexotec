// Mirrors app/schemas/{user,customer,auth}.py — camelCase JSON, as the
// backend's CamelModel convention produces.

// dealer_admin is gone (WP-2 PR-2, Roles & Permissions RP-1) — replaced by
// isDealerManager below, orthogonal to this set.
export type AccessRole =
  | 'platform_admin'
  | 'sales'
  | 'aftersales'
  | 'parts'
  | 'inventory'
  | 'finance'
  | 'technician'
  | 'auditor'
export type UserStatus = 'invited' | 'active' | 'suspended' | 'deactivated'

export interface UserRead {
  id: string
  dealershipId: string
  firstName: string
  lastName: string
  email: string
  phone: string | null
  role: string
  accessRoles: AccessRole[]
  isDealerManager: boolean
  employmentStatus: string
  authIdentityId: string
  status: UserStatus
  version: number
  createdAt: string
  updatedAt: string
}

// WP-3 PR-3: what the sidebar's dealership switcher renders. The frontend
// never decodes the session cookie itself (see api/client.ts), so this is
// the only place it learns Principal.memberships/tenantId.
export interface DealershipMembershipSummary {
  id: string
  legalName: string
}

export interface LoginResponse {
  user: UserRead
  activeDealership: DealershipMembershipSummary
  memberships: DealershipMembershipSummary[]
}

// Customer PRD Phase B (D-01 through D-16): customer_type gates which
// fields apply, language/customerNumber are mandatory+server-assigned, and
// the flat email/phone/preferredContactMethod fields are gone — phones and
// emails are multi-valued child collections, and preferredChannel is the
// only contact-preference field.
export type CustomerType = 'individual' | 'business'
export type Language = 'de' | 'fr' | 'it' | 'en'
export type Salutation = 'herr' | 'frau' | 'firma' | 'neutral'
export type LegalForm = 'ag' | 'gmbh' | 'einzelfirma' | 'verein' | 'genossenschaft' | 'weitere'
export type PreferredChannel = 'mail' | 'call' | 'message' | 'letter'
export type PhoneType = 'mobile' | 'private' | 'office'
export type EmailType = 'private' | 'business'
export type CustomerLifecycleStatus = 'prospect' | 'active' | 'inactive' | 'merged' | 'do_not_contact'
export type CustomerSource = 'walk_in' | 'phone' | 'web_lead' | 'marketplace' | 'other'

export interface CustomerAddress {
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  country: string
}

export interface CustomerAddressRead extends CustomerAddress {
  canton: string | null
}

export interface CustomerRead {
  id: string
  groupId: string
  customerNumber: string
  customerType: CustomerType
  language: Language
  salutation: Salutation | null
  firstName: string | null
  lastName: string | null
  birthDate: string | null
  nationality: string | null
  companyName: string | null
  legalForm: LegalForm | null
  preferredChannel: PreferredChannel | null
  address: CustomerAddressRead | null
  lifecycleStatus: CustomerLifecycleStatus
  source: CustomerSource | null
  sourceRef: string | null
  duplicateOfCustomerId: string | null
  marketingConsent: boolean
  version: number
  createdAt: string
  updatedAt: string
}

export interface CustomerPage {
  items: CustomerRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
}

// ADR-067's "six facts every contact-channel row carries" (label,
// isPrimary, validFrom/validTo, doNotUse/doNotUseReason, consent*) —
// app/customer/schemas/customer.py's ContactChannelMixin-backed schemas
// have carried all of these since WP-3 PR-5; this file was stale relative
// to that until WP-6c PR-10 caught up (found while building
// RepeatableRowGroup against what the API actually returns).
export interface CustomerPhoneCreate {
  phoneType: PhoneType
  label?: string | null
  phoneE164: string
  isPrimary?: boolean
}

export interface CustomerEmailCreate {
  emailType: EmailType
  label?: string | null
  emailAddress: string
  isPrimary?: boolean
}

export interface CustomerPhoneRead {
  id: string
  customerId: string
  phoneType: PhoneType
  label: string | null
  phoneE164: string
  isPrimary: boolean
  validFrom: string
  validTo: string | null
  doNotUse: boolean
  doNotUseReason: string | null
  consentGranted: boolean
  consentSource: string | null
  consentTimestamp: string | null
  createdAt: string
  updatedAt: string
}

export interface CustomerEmailRead {
  id: string
  customerId: string
  emailType: EmailType
  label: string | null
  emailAddress: string
  isPrimary: boolean
  validFrom: string
  validTo: string | null
  doNotUse: boolean
  doNotUseReason: string | null
  consentGranted: boolean
  consentSource: string | null
  consentTimestamp: string | null
  createdAt: string
  updatedAt: string
}

export interface CustomerPhonePage {
  items: CustomerPhoneRead[]
}

export interface CustomerEmailPage {
  items: CustomerEmailRead[]
}

export interface CustomerPhoneUpdate {
  phoneType?: PhoneType
  label?: string | null
  phoneE164?: string
  isPrimary?: boolean
  validTo?: string | null
  doNotUse?: boolean
  doNotUseReason?: string | null
  consentGranted?: boolean
  consentSource?: string | null
}

export interface CustomerEmailUpdate {
  emailType?: EmailType
  label?: string | null
  emailAddress?: string
  isPrimary?: boolean
  validTo?: string | null
  doNotUse?: boolean
  doNotUseReason?: string | null
  consentGranted?: boolean
  consentSource?: string | null
}

// customerType is immutable after creation and customerNumber is
// server-assigned — neither is settable here (matches CustomerCreate /
// CustomerUpdate in app/schemas/customer.py).
export interface CustomerCreateInput {
  customerType: CustomerType
  language: Language
  salutation?: Salutation | null
  firstName?: string | null
  lastName?: string | null
  birthDate?: string | null
  nationality?: string | null
  companyName?: string | null
  legalForm?: LegalForm | null
  taxId?: string | null
  preferredChannel?: PreferredChannel | null
  phones?: CustomerPhoneCreate[]
  emails?: CustomerEmailCreate[]
  address?: CustomerAddress | null
  lifecycleStatus?: CustomerLifecycleStatus
  source?: CustomerSource | null
  sourceRef?: string | null
  marketingConsent?: boolean
}

export interface CustomerUpdateInput {
  language?: Language
  salutation?: Salutation | null
  firstName?: string | null
  lastName?: string | null
  birthDate?: string | null
  nationality?: string | null
  companyName?: string | null
  legalForm?: LegalForm | null
  taxId?: string | null
  preferredChannel?: PreferredChannel | null
  address?: CustomerAddress | null
  lifecycleStatus?: CustomerLifecycleStatus
  source?: CustomerSource | null
  sourceRef?: string | null
  marketingConsent?: boolean
}

// Mirrors app/schemas/audit.py. before/after are raw snapshots (not a
// diff) — the UI computes which keys changed for display.
export interface AuditEventRead {
  id: string
  entityType: string
  entityId: string
  tenantId: string | null
  action: string
  actorId: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  reason: string | null
  createdAt: string
}

export interface AuditEventPage {
  items: AuditEventRead[]
  nextCursor: string | null
}

// Mirrors app/models/vehicle.py VehiclePartyRole / app/schemas/customer.py
// CustomerVehicle* — the customer-360 Vehicles tab (D-12).
export type VehiclePartyRole = 'owner' | 'keeper' | 'driver'

export interface VehiclePartySummary {
  id: string
  vin: string
  make: string
  model: string
  modelYear: number
  trim: string | null
}

export interface CustomerVehicleRead {
  id: string
  customerId: string
  vehicleId: string
  role: VehiclePartyRole
  effectiveFrom: string
  effectiveTo: string | null
  vehicle: VehiclePartySummary
  createdAt: string
  updatedAt: string
}

export interface CustomerVehiclePage {
  items: CustomerVehicleRead[]
}

// Mirrors app/schemas/customer.py CustomerExternalId* (per-dealer CRM/OEM
// linkage, FR-06 External IDs tab).
export interface CustomerExternalIdRead {
  id: string
  customerId: string
  systemName: string
  externalId: string
  createdAt: string
  updatedAt: string
}

export interface CustomerExternalIdPage {
  items: CustomerExternalIdRead[]
}

// Mirrors app/models/transaction.py / app/schemas/transaction.py.
export type TransactionType = 'sale' | 'trade_in'
export type TransactionStatus = 'draft' | 'completed' | 'cancelled'

export interface TransactionRead {
  id: string
  tenantId: string
  transactionType: TransactionType
  status: TransactionStatus
  customerId: string
  vehicleId: string
  primaryUserId: string
  amount: string | null
  transactionDate: string | null
  externalRef: string | null
  notes: string | null
  version: number
  createdAt: string
  updatedAt: string
  createdBy: string | null
  updatedBy: string | null
}

export interface TransactionPage {
  items: TransactionRead[]
  nextCursor: string | null
}

// Mirrors app/schemas/customer.py CustomerDuplicateCandidate (D-07: name
// fields optional so a business candidate doesn't break the shape).
export type DuplicateMatchKind = 'exact' | 'similar'

export interface CustomerDuplicateCandidate {
  id: string
  customerNumber: string
  customerType: CustomerType
  firstName: string | null
  lastName: string | null
  companyName: string | null
  primaryPhone: string | null
  primaryEmail: string | null
  lifecycleStatus: CustomerLifecycleStatus
  match: DuplicateMatchKind
}

export interface CustomerDuplicateCandidateList {
  items: CustomerDuplicateCandidate[]
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: Record<string, unknown> | null
  }
}

// WP-5 PR-8 — master-data admin (mapping-gap queue)
export interface MappingGapRead {
  id: string
  provider: string
  vehicleKind: string
  codeGroup: string
  providerCode: string
  firstSeenAt: string
  lastSeenAt: string
  occurrences: number
  resolved: boolean
  resolvedAt: string | null
  resolvedValueCode: string | null
}

export interface MappingGapPage {
  items: MappingGapRead[]
  nextCursor: string | null
}

// WP-5 PR-9 — VehicleMdm identity, one-search-box, party allocation
// VehiclePartyRole is already defined above (line 224) — reused as-is,
// the vehicle-mdm party endpoints share the same customer.public enum.
export type VehicleStatus = 'active' | 'exported' | 'scrapped' | 'stolen'
export type CatalogueMatchStatus = 'matched' | 'unverified'
export type OdometerSource = 'service_visit' | 'sale' | 'valuation' | 'manual' | 'import'

export interface VehicleMdmRead {
  id: string
  vin: string
  vehicleNumber: string
  stammnummer: string | null
  typeApprovalNumber: string | null
  firstRegistrationDate: string | null
  catalogueVariantId: string | null
  catalogueMatchStatus: CatalogueMatchStatus
  vehicleStatus: VehicleStatus
  mergedIntoVehicleId: string | null
  version: number
  createdAt: string
  updatedAt: string
}

export interface VehicleMdmCreateResult {
  created: boolean
  vehicle: VehicleMdmRead
}

export interface VehicleMdmPage {
  items: VehicleMdmRead[]
  nextCursor: string | null
}

export interface VehiclePickerCandidate {
  id: string
  vehicleNumber: string
  vin: string
  plate: string | null
  plateGroupId: string | null
  isConflict: boolean
}

export interface VehicleSearchResult {
  resolved: VehicleMdmRead | null
  pickerCandidates: VehiclePickerCandidate[]
  filtered: VehicleMdmPage
}

export interface VehiclePartyAllocationRead {
  id: string
  vehicleId: string
  customerId: string
  role: VehiclePartyRole
  effectiveFrom: string
  effectiveTo: string | null
}

export interface VehiclePlateRead {
  id: string
  plate: string
  canton: string
  validFrom: string
  validTo: string | null
  isInterchangeable: boolean
  plateGroupId: string | null
}

export interface VehicleOdometerReadingRead {
  id: string
  value: number
  readingDate: string
  source: OdometerSource
  implausible: boolean
}

export interface VehicleAccessoryRead {
  id: string
  accessoryType: string
  description: string | null
  validFrom: string
  validTo: string | null
}

// WP-7 PR-1 (ADR-054): lifecycleStatus and reservationState are always
// independent — every combination is legal, including pipeline+reserved
// (a factory order already sold). "sold" is never a value here — a sold
// (invoiced) item is simply absent from the active list (FR-I-12).
export type StockLifecycleStatus = 'pipeline' | 'in_stock' | 'storno_pending'
export type StockReservationState = 'none' | 'reserved'
export type StockItemCondition = 'new' | 'used' | 'demo' | 'tagesz'
// WP-7 PR-7 — fixed, never dealer-configurable (that's Dealership.
// ageingAlertThresholds, a completely separate, notification-only field).
export type AgeingBucket = 'green' | 'amber' | 'red'

export interface StockItemRead {
  id: string
  stockNumber: string
  vehicleId: string | null
  vin: string | null
  vehicleLabel: string
  lifecycleStatus: StockLifecycleStatus
  reservationState: StockReservationState
  condition: StockItemCondition
  locationId: string | null
  odometerKm: number | null
  listPrice: string | null
  effectivePrice: string | null
  firstRegistrationDate: string | null
  pipelineRef: string | null
  orderDate: string | null
  expectedDelivery: string | null
  inStockAt: string | null
  // WP-7 PR-3 (ADR-057) — no vatTreatment field here or anywhere else.
  supplierName: string | null
  supplierIsVatRegistered: boolean | null
  purchaseDate: string | null
  purchasePrice: string | null
  purchaseInvoiceRef: string | null
  landedCost: string | null
  notionalInputTaxApplicable: boolean | null
  notionalInputTaxRate: string | null
  notionalInputTaxAmount: string | null
  notionalInputTaxOverridden: boolean
  isInvoiceable: boolean
  leftStockAt: string | null
  ageingBucket: AgeingBucket | null
  basePrice: string | null
  valuationRefId: string | null
  valuationRefAmount: string | null
  version: number
  createdAt: string
  updatedAt: string
}

// WP-7 PR-7 (ADR-055) — a deliberately SEPARATE shape from StockItemRead,
// never that interface with fields picked out. Absent by construction:
// effectivePrice, landedCost, notionalInputTax*, purchasePrice,
// supplierName, isInvoiceable, and anything Wagenbuch/publishing-shaped.
export interface StockItemGroupRead {
  id: string
  dealershipId: string
  dealershipLabel: string
  stockNumber: string
  vin: string | null
  vehicleLabel: string
  lifecycleStatus: StockLifecycleStatus
  reservationState: StockReservationState
  condition: StockItemCondition
  odometerKm: number | null
  listPrice: string | null
  firstRegistrationDate: string | null
  updatedAt: string
}

export interface StockItemGroupPage {
  items: StockItemGroupRead[]
}

export interface StockItemPage {
  items: StockItemRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
}

// WP-7 PR-6 — the Wagenbuch (ADR-029). Costs and revenues, in this exact
// order to match app.inventory.models.stock_item_ledger.LedgerCategory.
export type LedgerCategory =
  | 'einstandspreis'
  | 'landed_cost'
  | 'aufbereitung'
  | 'reparatur'
  | 'gutachten'
  | 'standkosten'
  | 'werbung'
  | 'garantie'
  | 'abwertung'
  | 'promotion'
  | 'sonstige_kosten'
  | 'verkaufserloes'
  | 'kickback'
  | 'zusatzerloes'
  | 'foerderung'
  | 'sonstige_ertraege'
export type LedgerDirection = 'cost' | 'revenue'

export interface LedgerEntryRead {
  id: string
  stockItemId: string
  category: LedgerCategory
  direction: LedgerDirection
  amount: string
  occurredAt: string
  sourceRef: string
  isAuto: boolean
  createdAt: string
}

export interface LedgerEntryPage {
  items: LedgerEntryRead[]
}

// WP-7 PR-8 (ADR-062) — three named channels, never a generic list.
export type MarketplaceChannel = 'autoscout24' | 'carmarket' | 'autolina'
export type PublishingState = 'not_published' | 'published'

export interface BlockingCondition {
  field: string
  message: string
}

export interface PublishingRead {
  id: string
  stockItemId: string
  channel: MarketplaceChannel
  state: PublishingState
  zusatztitel: string | null
  bemerkungen: string | null
  zustandsbeschreibung: string | null
  haendlerbemerkungen: string | null
  youtubeUrl: string | null
  pdfDocumentRef: string | null
  lastPublishedAt: string | null
  blockingConditions: BlockingCondition[]
  version: number
}

export interface MediaRead {
  id: string
  position: number
  url: string
}

// WP-8 PR-1 (S-D01) — offer/contract as two linked entities. offerStatus
// spans draft/open/cancelled, contractStatus separately spans
// pending/confirmed/cancelled/invoiced — the grid's shared STATUS column
// stores whichever applies as a plain string (see SalesDealRead), never a
// merged enum.
export type SalesOfferStatus = 'draft' | 'open' | 'cancelled'
export type SalesContractStatus = 'pending' | 'confirmed' | 'cancelled' | 'invoiced'

// WP-8 PR-2 — server-computed, never client-derived (FR-S-05); the sticky
// footer's missing-requirements list and each container's own status
// badge both read from this same shape.
export interface OfferContainerState {
  id: 'customer' | 'vehicle' | 'pricing' | 'trade_in' | 'leasing'
  requirement: 'required' | 'optional'
  status: 'not_started' | 'in_progress' | 'complete' | 'placeholder'
}

export interface SalesOfferRead {
  id: string
  offerNumber: string
  status: SalesOfferStatus
  customerId: string | null
  customerLabel: string | null
  customerLocality: string | null
  vehicleSource: 'stock' | 'manual' | null
  stockItemId: string | null
  vehicleLabel: string | null
  manualVehicleCondition: string | null
  manualBasePrice: string | null
  leasingDownPayment: string | null
  leasingTermMonths: number | null
  leasingKmPerYear: number | null
  basePrice: string | null
  optionsTotal: string | null
  listPrice: string | null
  accessoriesTotal: string | null
  totalBeforeDiscount: string | null
  discountType: 'percent' | 'amount' | null
  discountValue: string | null
  discountAmount: string | null
  grossPrice: string | null
  costBasis: string | null
  margin: string | null
  vehicleSnapshotFrozenAt: string | null
  tradeInVehicleId: string | null
  tradeInLabel: string | null
  tradeInVin: string | null
  tradeInValuationId: string | null
  tradeInValue: string | null
  tradeInPurchasePrice: string | null
  payable: string | null
  cancelledReason: string | null
  containers: OfferContainerState[]
  version: number
  createdAt: string
  updatedAt: string
}

export interface SalesOfferPage {
  items: SalesOfferRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
}

export interface SalesContractRead {
  id: string
  contractNumber: string
  offerId: string | null
  offerNumber: string | null
  status: SalesContractStatus
  customerId: string | null
  customerLabel: string | null
  customerLocality: string | null
  stockItemId: string | null
  vehicleLabel: string | null
  grossPrice: string | null
  margin: string | null
  cancelledReason: string | null
  version: number
  createdAt: string
  updatedAt: string
}

export interface SalesContractPage {
  items: SalesContractRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
}

// The overview grid's own read shape (ADR-060) — a deliberately separate
// schema from SalesOfferRead/SalesContractRead, mirroring
// app.inventory's StockItemGroupRead convention for a read model.
export interface SalesDealRead {
  id: string
  entityType: 'offer' | 'contract'
  number: string
  status: string
  offerId: string | null
  offerNumber: string | null
  contractId: string | null
  contractNumber: string | null
  customerId: string | null
  customerLabel: string | null
  customerLocality: string | null
  vehicleLabel: string | null
  grossPrice: string | null
  margin: string | null
  documentsCount: number
  createdAt: string
  updatedAt: string
}

export interface SalesDealPage {
  items: SalesDealRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
}
