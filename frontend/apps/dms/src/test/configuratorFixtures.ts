import { expect } from 'vitest'
import { fireEvent, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import { CODED_FIELDS } from '../configurationOptions'
import type { FakeRoute } from './fakeBackend'
import type { CatalogueVariantRead, ConfigurationRead, IdentificationRead } from '../api/types'

/** C-D / C-F render tests — one configuration as the backend returns it. */
export function configurationRead(overrides: Partial<ConfigurationRead> = {}): ConfigurationRead {
  return {
    id: 'cfg-1',
    tenantId: 't1',
    source: 'provider',
    mode: 'build',
    catalogueMatchStatus: 'matched',
    matchMethod: 'catalogue_browse',
    catalogueVariantId: 'v1',
    catalogueVariantLabel: 'Volkswagen Golf Golf GTI',
    vehicleId: null,
    vehicleLabel: null,
    vin: null,
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: null,
    licencePlate: null,
    mileageKm: null,
    brandDisplayName: 'Volkswagen',
    modelGroupName: 'Golf',
    variantName: 'Golf GTI',
    vehicleKind: null,
    fuelType: null,
    bodyStyle: null,
    drivetrain: null,
    transmission: null,
    exteriorColour: null,
    interiorColour: null,
    exteriorColourSurcharge: null,
    interiorColourSurcharge: null,
    wheels: null,
    wheelsSurcharge: null,
    spec: { ps: 245 },
    overriddenFields: [],
    options: [],
    notes: null,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function catalogueVariant(overrides: Partial<CatalogueVariantRead> = {}): CatalogueVariantRead {
  return {
    id: 'v1',
    brandId: 'b1',
    brandDisplayName: 'Volkswagen',
    modelGroupId: 'g1',
    modelGroupName: 'Golf',
    variantName: 'Golf GTI',
    modelYearFrom: 2021,
    modelYearTo: null,
    inProduction: true,
    vehicleKind: 'passenger_car',
    fuelType: 'petrol',
    bodyStyle: 'hatchback',
    drivetrain: 'fwd',
    transmission: 'automatic',
    typeApprovalNumbers: ['2CD456'],
    currentPrice: { amount: '42500.00', year: 2021, isNet: false },
    spec: { ps: 245, displacementCcm: 1984 },
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function identification(overrides: Partial<IdentificationRead> = {}): IdentificationRead {
  return {
    kind: 'typenschein',
    matchMethod: 'typenschein',
    outcome: 'none',
    observed: {
      vin: null,
      licencePlate: null,
      stammnummer: null,
      typeApprovalNumber: null,
      firstRegistrationDate: null,
      werkscode: null,
    },
    existingVehicle: null,
    variants: [],
    plateRecords: [],
    plateRecordsInterchangeable: false,
    plateRecordsConflict: false,
    bestMatchAvailable: false,
    notes: [],
    ...overrides,
  }
}

export const GOLF_CANDIDATE = {
  catalogueVariantId: 'v1',
  brandDisplayName: 'Volkswagen',
  modelGroupName: 'Golf',
  variantName: 'Golf GTI',
  modelYearFrom: 2021,
  modelYearTo: null,
  ps: 245,
  kw: 180,
  basePrice: '42500.00',
  basePriceYear: 2021,
  newPriceForYear: null,
}

// Anto, 2026-10-12 — a manual configuration needs all five coded fields
// before it can be saved. A render test that saves one serves these lists
// (put `CODED_REFERENCE_ROUTES` before any catch-all `/reference-data/`
// route) and picks a value of each with `pickCodedFields`.
const CODED_REFERENCE_VALUES: Record<string, [string, string, string, string, string]> = {
  vehicle_kind: ['passenger_car', 'Personenwagen', 'Voiture de tourisme', 'Autovettura', 'Passenger car'],
  fuel_type: ['petrol', 'Benzin', 'Essence', 'Benzina', 'Petrol'],
  body_style: ['sedan', 'Limousine', 'Berline', 'Berlina', 'Sedan'],
  drivetrain: ['fwd', 'Frontantrieb', 'Traction avant', 'Trazione anteriore', 'Front-wheel drive'],
  transmission: ['manual', 'Schaltgetriebe', 'Boîte manuelle', 'Cambio manuale', 'Manual'],
}

/** The coded values `pickCodedFields` saves, as the save body carries them. */
export const PICKED_CODED_VALUES = {
  vehicleKind: 'passenger_car',
  fuelType: 'petrol',
  bodyStyle: 'sedan',
  drivetrain: 'fwd',
  transmission: 'manual',
}

export const CODED_REFERENCE_ROUTES: FakeRoute[] = Object.entries(CODED_REFERENCE_VALUES).map(
  ([listCode, [valueCode, labelDe, labelFr, labelIt, labelEn]]) => ({
    method: 'GET',
    match: new RegExp(`/reference-data/${listCode}$`),
    handler: () => ({
      items: [{ id: `rv-${valueCode}`, listCode, valueCode, labelDe, labelFr, labelIt, labelEn, sortOrder: 0, active: true, version: 1 }],
      nextCursor: null,
    }),
  }),
)

/** Picks the one value `CODED_REFERENCE_ROUTES` offers in each of a manual
 * configuration's five coded selects (German UI labels). Plain DOM events,
 * not `userEvent`: ten pointer sequences per call would eat the test's time
 * budget, and the tests that call this are about what happens after. */
export async function pickCodedFields(scope: HTMLElement = document.body): Promise<void> {
  // A direct walk of the `<label>`s and the field's own listbox, not the
  // Testing Library queries: on the configurator's large form each of those
  // scans the whole document (~1 s per field here), and five of them eat the
  // test's time budget.
  for (const f of CODED_FIELDS) {
    const text = i18n.t(f.labelKey)
    const valueLabel = CODED_REFERENCE_VALUES[f.listCode][1]
    const field = await waitFor(() => {
      // the label carries the required marker (` *`)
      const label = Array.from(scope.querySelectorAll('label')).find(
        (l) => l.textContent?.replace(/\s*\*\s*$/, '').trim() === text,
      )
      const input = label?.htmlFor ? (document.getElementById(label.htmlFor) as HTMLInputElement | null) : null
      if (!input) throw new Error(`no coded field labelled ${text}`)
      return input
    })
    fireEvent.click(field)
    const option = await waitFor(() => {
      const listbox = document.getElementById(field.getAttribute('aria-controls') ?? '')
      const match = Array.from(listbox?.querySelectorAll('[role="option"]') ?? []).find(
        (o) => o.textContent?.trim() === valueLabel,
      )
      if (!match) throw new Error(`no option ${valueLabel} for ${text}`)
      return match
    })
    fireEvent.click(option)
    await waitFor(() => expect(field).toHaveValue(valueLabel))
  }
}
