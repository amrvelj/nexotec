// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { CatalogueBrowseGrid } from './CatalogueBrowseGrid'

// KAN-160 exit criterion 2: the catalogue's price column renders through
// formatCurrencyChf, and the ui-kit DataGrid's footer total through the
// kit's own formatNumber — `12'500`-style under the French UI, never the
// runtime's fr-CH CLDR data (narrow-space grouping, comma decimal).

// jsdom has no layout → TanStack Virtual renders zero rows. Mock it so the
// grid actually shows its rows (same mock as ConfiguratorOverlay's test).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const VARIANT = {
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
  typeApprovalNumbers: [],
  currentPrice: { amount: '42500.00', year: 2021, isNet: false },
  spec: { ps: 245, displacementCcm: 1984, trimName: 'GTI' },
  updatedAt: '2026-01-01T00:00:00Z',
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('CatalogueBrowseGrid', () => {
  it("renders the price as CHF 42'500.00 and the footer total as 12'500 under the French UI", async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
      {
        method: 'GET',
        match: /\/catalogue\/facets$/,
        handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }),
      },
      {
        method: 'GET',
        match: /\/catalogue\/variants$/,
        handler: () => ({ items: [VARIANT], nextCursor: null, total: 12500, totalIsEstimate: false, browseAvailable: true }),
      },
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />)

    expect(await screen.findByText("CHF 42'500.00")).toBeInTheDocument()
    expect(screen.getByText((_, el) => el?.tagName === 'SPAN' && /12'500/.test(el.textContent ?? ''))).toBeInTheDocument()
  })
})
