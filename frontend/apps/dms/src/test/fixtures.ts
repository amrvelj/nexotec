import type {
  CustomerAddressRead,
  CustomerEmailRead,
  CustomerPhoneRead,
  CustomerRead,
  EmailType,
  PhoneType,
} from '../api/types'

// Minimal, valid domain objects for render tests. Every field the screens
// actually read is populated; the rest gets a sane default so a test only
// has to spell out what it is asserting on.

export function customer(overrides: Partial<CustomerRead> = {}): CustomerRead {
  return {
    id: 'cust-1',
    groupId: 'group-1',
    customerNumber: 'K-1001',
    customerType: 'individual',
    language: 'de',
    salutation: 'herr',
    firstName: 'Hans',
    lastName: 'Muster',
    birthDate: null,
    nationality: null,
    companyName: null,
    legalForm: null,
    preferredChannel: null,
    address: null,
    lifecycleStatus: 'active',
    source: null,
    sourceRef: null,
    duplicateOfCustomerId: null,
    marketingConsent: false,
    creditBlock: false,
    creditBlockReason: null,
    creditBlockedAt: null,
    createdBy: null,
    updatedBy: null,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-02-01T00:00:00Z',
    ...overrides,
  }
}

let phoneSeq = 0
export function phone(overrides: Partial<CustomerPhoneRead> & { type?: PhoneType; value?: string } = {}): CustomerPhoneRead {
  phoneSeq += 1
  const { type, value, ...rest } = overrides
  return {
    id: `phone-${phoneSeq}`,
    customerId: 'cust-1',
    phoneType: type ?? 'mobile',
    label: null,
    phoneE164: value ?? '+41 79 000 00 00',
    isPrimary: false,
    validFrom: '2026-01-01T00:00:00Z',
    validTo: null,
    doNotUse: false,
    doNotUseReason: null,
    consentGranted: false,
    consentSource: null,
    consentTimestamp: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...rest,
  }
}

let emailSeq = 0
export function email(overrides: Partial<CustomerEmailRead> & { type?: EmailType; value?: string } = {}): CustomerEmailRead {
  emailSeq += 1
  const { type, value, ...rest } = overrides
  return {
    id: `email-${emailSeq}`,
    customerId: 'cust-1',
    emailType: type ?? 'personal',
    label: null,
    emailAddress: value ?? 'hans.muster@example.ch',
    isPrimary: false,
    validFrom: '2026-01-01T00:00:00Z',
    validTo: null,
    doNotUse: false,
    doNotUseReason: null,
    consentGranted: false,
    consentSource: null,
    consentTimestamp: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...rest,
  }
}

export function customerAddress(overrides: Partial<CustomerAddressRead> = {}): CustomerAddressRead {
  return {
    id: 'address-1',
    customerId: 'cust-1',
    addressType: 'domicile',
    label: null,
    addressStreet: 'Bahnhofstrasse',
    addressLine2: null,
    addressHouseNumber: '1',
    addressPostalCode: '8001',
    addressLocality: 'Zürich',
    addressCanton: 'ZH',
    addressCountry: 'CH',
    isPrimary: true,
    validFrom: '2026-01-01T00:00:00Z',
    validTo: null,
    doNotUse: false,
    doNotUseReason: null,
    consentGranted: false,
    consentSource: null,
    consentTimestamp: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function customerPage(items: CustomerRead[]): {
  items: CustomerRead[]
  nextCursor: string | null
  total: number
  totalIsEstimate: boolean
} {
  return { items, nextCursor: null, total: items.length, totalIsEstimate: false }
}
