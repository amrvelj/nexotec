import type {
  LabourRateProposalRead,
  ServiceOrderHistoryRead,
  ServiceOrderPositionRead,
  ServiceOrderRead,
  ServiceOrderSummary,
} from '../../../api/types'

// KAN-810 — the workshop order as the API answers it (app/aftersales/schemas/service_order.py).
// Every figure is the server's; the screens only show them.

export const ORDER_ID = 'order-1'
export const JOB_ID = 'job-1'

export function summary(overrides: Partial<ServiceOrderSummary> = {}): ServiceOrderSummary {
  return {
    id: ORDER_ID,
    version: 3,
    orderNumber: 'WA-2026-0406',
    state: 'accepted',
    orderDate: '2026-11-26',
    locationId: 'loc-1',
    locationLabel: 'Aarau',
    contact: { contactId: 'contact-1', label: 'Reto Wyss' },
    vehicleId: 'vehicle-1',
    vehicleLabel: 'Mercedes-Benz GLC · W1KBMRB80MN6DTN3X',
    waitingForAnswer: false,
    createdAt: '2026-11-26T08:10:00Z',
    ...overrides,
  }
}

export const LABOUR_BY_TIME: ServiceOrderPositionRead = {
  id: 'pos-1',
  jobId: JOB_ID,
  sortIndex: 1,
  kind: 'labour_time',
  state: 'open',
  text: 'Jahresservice nach Herstellervorgabe',
  labourCategoryId: 'cat-1',
  labourCategoryLabel: 'Mechanik',
  time: { quantity: '9.00', unit: 'work_units', workUnitsPerHour: '12', hours: '0.7500' },
  unitPrice: { amount: '100.0000', entered: 'net', vatRate: '8.10', net: '100.0000', gross: '108.1000' },
  lineNet: '75.00',
  lineGross: '81.08',
  rateSource: 'category_rate',
  labourRateId: 'rate-1',
  rateValidFrom: '2026-11-01',
  pricedAt: '2026-11-26T08:12:00Z',
  pricedBy: 'user-1',
}

export const TEXT_POSITION: ServiceOrderPositionRead = {
  id: 'pos-2',
  jobId: JOB_ID,
  sortIndex: 2,
  kind: 'text',
  state: 'open',
  text: 'Ersatzwagen bis 17 Uhr',
  labourCategoryId: null,
  labourCategoryLabel: null,
  time: null,
  unitPrice: null,
  lineNet: null,
  lineGross: null,
  rateSource: null,
  labourRateId: null,
  rateValidFrom: null,
  pricedAt: null,
  pricedBy: null,
}

export function order(overrides: Partial<ServiceOrderRead> = {}): ServiceOrderRead {
  return {
    ...summary(),
    invoiceRecipient: { contactId: 'contact-1', label: 'Reto Wyss' },
    driver: null,
    jobs: [
      {
        id: JOB_ID,
        sortIndex: 1,
        name: 'Jahresservice Flotte',
        billingCategory: 'customer',
        answerState: 'accepted',
        answeredAt: '2026-11-26T08:20:00Z',
        answeredBy: 'user-1',
        positions: [LABOUR_BY_TIME, TEXT_POSITION],
      },
    ],
    totals: {
      accepted: [
        {
          payer: 'customer',
          net: '434.85',
          vatLines: [{ rate: '8.10', net: '434.85', vat: '35.22' }],
          vat: '35.22',
          total: '470.07',
          rounding: '-0.02',
          roundedTotal: '470.05',
        },
      ],
      open: [
        {
          payer: 'customer',
          net: '120.00',
          vatLines: [{ rate: '8.10', net: '120.00', vat: '9.72' }],
          vat: '9.72',
          total: '129.72',
          rounding: '-0.02',
          roundedTotal: '129.70',
        },
      ],
    },
    cancellation: null,
    updatedAt: '2026-11-26T08:20:00Z',
    ...overrides,
  }
}

export const HISTORY: ServiceOrderHistoryRead = {
  items: [
    {
      id: 'h-1',
      action: 'service_order_created',
      actorId: 'user-2',
      actorLabel: 'Sandra Keller',
      before: null,
      after: { orderNumber: 'WA-2026-0406', state: 'draft', contactLabel: 'Reto Wyss' },
      reason: null,
      createdAt: '2026-11-26T08:10:00Z',
    },
    {
      id: 'h-2',
      action: 'service_order_parties_changed',
      actorId: 'user-3',
      actorLabel: 'Marc Perrin',
      before: { invoiceRecipientLabel: 'Reto Wyss', state: 'draft' },
      after: { invoiceRecipientLabel: 'Pharmacie du Lac SA', state: 'draft' },
      reason: null,
      createdAt: '2026-11-26T09:30:00Z',
    },
  ],
}

export function proposal(overrides: Partial<LabourRateProposalRead> = {}): LabourRateProposalRead {
  return {
    labourCategoryId: 'cat-1',
    found: true,
    source: 'agreement',
    labourRateId: null,
    validFrom: '2026-11-01',
    unitPrice: { amount: '148.0000', entered: 'net', vatRate: '8.10', net: '148.0000', gross: '159.9880' },
    timeUnit: 'work_units',
    workUnitsPerHour: '12',
    ...overrides,
  }
}

export const LABOUR_CATEGORIES = {
  today: '2026-11-26',
  items: [
    {
      id: 'cat-1',
      name: 'Mechanik',
      nameTranslations: { fr: 'Mécanique' },
      isActive: true,
      version: 1,
      createdAt: '2026-11-01T00:00:00Z',
      rates: [],
      members: [],
    },
    {
      id: 'cat-2',
      name: 'Karosserie',
      nameTranslations: {},
      isActive: true,
      version: 1,
      createdAt: '2026-11-01T00:00:00Z',
      rates: [],
      members: [],
    },
  ],
}

/** A fixed-price labour position without a time (KAN-810 review: its time can be added on edit). */
export const LABOUR_FIXED_NO_TIME: ServiceOrderPositionRead = {
  ...LABOUR_BY_TIME,
  id: 'pos-1',
  kind: 'labour_fixed',
  text: 'Pauschale Bremsen',
  time: null,
  unitPrice: { amount: '250.0000', entered: 'net', vatRate: '8.10', net: '250.0000', gross: '270.2500' },
  lineNet: '250.00',
  lineGross: '270.25',
  rateSource: 'typed',
  labourRateId: null,
}
