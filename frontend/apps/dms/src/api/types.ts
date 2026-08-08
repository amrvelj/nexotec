// Mirrors app/schemas/{user,customer,auth}.py — camelCase JSON, as the
// backend's CamelModel convention produces.

export type AccessRole = 'platform_admin' | 'dealer_admin' | 'sales' | 'inventory' | 'auditor'
export type UserStatus = 'invited' | 'active' | 'suspended' | 'deactivated'

export interface UserRead {
  id: string
  dealerId: string
  firstName: string
  lastName: string
  email: string
  phone: string | null
  role: string
  accessRole: AccessRole
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

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: Record<string, unknown> | null
  }
}
