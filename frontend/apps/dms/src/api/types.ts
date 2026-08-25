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

export interface LoginResponse {
  user: UserRead
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
  tenantId: string
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

export interface CustomerPhoneCreate {
  phoneType: PhoneType
  phoneE164: string
  isPrimary?: boolean
}

export interface CustomerEmailCreate {
  emailType: EmailType
  emailAddress: string
  isPrimary?: boolean
}

export interface CustomerPhoneRead {
  id: string
  customerId: string
  phoneType: PhoneType
  phoneE164: string
  isPrimary: boolean
  createdAt: string
  updatedAt: string
}

export interface CustomerEmailRead {
  id: string
  customerId: string
  emailType: EmailType
  emailAddress: string
  isPrimary: boolean
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
  phoneE164?: string
  isPrimary?: boolean
}

export interface CustomerEmailUpdate {
  emailType?: EmailType
  emailAddress?: string
  isPrimary?: boolean
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
