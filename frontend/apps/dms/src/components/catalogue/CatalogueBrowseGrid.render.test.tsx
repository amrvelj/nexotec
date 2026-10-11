// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRequest, type FakeRoute } from '../../test/fakeBackend'
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

// KAN-239 — the API caps a page at 100 (`le: 100`) and answers anything
// above with 422, exactly as this fake does. The grid once asked for the
// reference lists with `limit=200`, swallowed the 422 and showed raw codes.
function capped(pages: unknown[][]) {
  return (req: FakeRequest) => {
    if (Number(req.params.get('limit') ?? '50') > 100) {
      return status(422, { code: 'validation_error', message: 'limit: less_than_equal', details: [] })
    }
    const index = Number(req.params.get('cursor') ?? '0')
    return { items: pages[index], nextCursor: index + 1 < pages.length ? String(index + 1) : null }
  }
}

const refValue = (valueCode: string, labelDe: string, labelFr: string) => ({
  id: `rv-${valueCode}`,
  valueCode,
  labelDe,
  labelFr,
  labelIt: labelDe,
  labelEn: labelDe,
  sortOrder: 0,
  active: true,
})

function catalogueRoutes(referenceData: FakeRoute[]): FakeRoute[] {
  return [
    {
      method: 'GET',
      match: /\/vehicle-mdm\/brands$/,
      handler: capped([[{ id: 'b0', displayName: 'Audi' }], [{ id: 'b1', displayName: 'Volkswagen' }]]),
    },
    { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: () => ({ items: [VARIANT], nextCursor: null, total: 1, totalIsEstimate: false, browseAvailable: true }),
    },
    ...referenceData,
    { method: 'GET', match: /\/reference-data\//, handler: capped([[]]) },
  ]
}

describe('CatalogueBrowseGrid reference labels (KAN-239)', () => {
  it('shows translated labels, fetched page by page within the API cap', async () => {
    await i18n.changeLanguage('fr')
    const backend = installFakeBackend(
      catalogueRoutes([
        {
          method: 'GET',
          match: /\/reference-data\/fuel_type$/,
          handler: capped([[refValue('diesel', 'Diesel', 'Diesel')], [refValue('petrol', 'Benzin', 'Essence')]]),
        },
        {
          method: 'GET',
          match: /\/reference-data\/body_style$/,
          handler: capped([[refValue('hatchback', 'Kompaktwagen', 'Compacte')]]),
        },
      ]),
    )

    renderWithProviders(<CatalogueBrowseGrid mode="build" />)

    expect(await screen.findByText('Essence')).toBeInTheDocument()
    expect(screen.getByText('Compacte')).toBeInTheDocument()
    expect(screen.queryByText('petrol')).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('catalogueBrowse.labelsLoadError.title'))).not.toBeInTheDocument()
    for (const call of backend.callsTo(/\/reference-data\//)) {
      expect(Number(call.params.get('limit'))).toBeLessThanOrEqual(100)
    }

    // The brand drill-down walks the same cap: a brand on the second page is offered.
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') }))
    expect(await screen.findByRole('option', { name: 'Volkswagen' })).toBeInTheDocument()
  })

  it('a list that fails to load marks its codes and says so, never showing them as labels', async () => {
    installFakeBackend(
      catalogueRoutes([
        { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => status(500, { code: 'internal', message: 'boom' }) },
      ]),
    )

    renderWithProviders(<CatalogueBrowseGrid mode="build" />)

    expect(await screen.findByText(i18n.t('catalogueBrowse.labelsLoadError.title'))).toBeInTheDocument()
    expect(screen.getByText('⚠ petrol')).toBeInTheDocument()
    expect(screen.queryByText('petrol')).not.toBeInTheDocument()
  })
})
