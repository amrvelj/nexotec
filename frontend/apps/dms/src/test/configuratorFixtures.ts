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
