// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
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

// KAN-548: the drill-down is live on the provider — a brand list per vehicle
// kind, the model groups of a brand, the facets per field, one page of
// variants — each a `state`; the grid asks for variants only once a brand is
// chosen (the route below carries `brand=Volkswagen`).
const BRANDS = { items: [{ name: 'Audi', brandNr: '1', models: ['A3'] }, { name: 'Volkswagen', brandNr: '2', models: ['Golf'] }], state: 'ok' }
const MODEL_GROUPS = { items: [{ key: '104', name: 'Golf (2019-)', modelSearch: 'GOLF', productionYearFrom: 2019, productionYearTo: null }], state: 'ok' }
const FACETS = { browseAvailable: true, state: 'ok', coded: {}, numeric: {} }
const PAGE = (total = 1) => ({ items: [VARIANT], nextCursor: null, total, totalIsEstimate: false, browseAvailable: true, state: 'ok' })
const SCOPED = '/catalogue?brand=Volkswagen'

describe('CatalogueBrowseGrid', () => {
  it("renders the price as CHF 42'500.00 and the footer total as 12'500 under the French UI", async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { method: 'GET', match: /\/catalogue\/brands$/, handler: () => BRANDS },
      { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => MODEL_GROUPS },
      { method: 'GET', match: /\/catalogue\/facets$/, handler: () => FACETS },
      { method: 'GET', match: /\/catalogue\/variants$/, handler: () => PAGE(12500) },
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })

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
    { method: 'GET', match: /\/catalogue\/brands$/, handler: () => BRANDS },
    { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => MODEL_GROUPS },
    { method: 'GET', match: /\/catalogue\/facets$/, handler: () => FACETS },
    { method: 'GET', match: /\/catalogue\/variants$/, handler: () => PAGE() },
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

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })

    expect(await screen.findByText('Essence')).toBeInTheDocument()
    // Body style is hidden by default (KAN-361): show it from the Columns panel.
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
    await user.click(screen.getByRole('checkbox', { name: i18n.t('catalogueBrowse.columns.bodyStyle') }))
    expect(screen.getByText('Compacte')).toBeInTheDocument()
    expect(screen.queryByText('petrol')).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('catalogueBrowse.labelsLoadError.title'))).not.toBeInTheDocument()
    for (const call of backend.callsTo(/\/reference-data\//)) {
      expect(Number(call.params.get('limit'))).toBeLessThanOrEqual(100)
    }

    // The brand drill-down lists the provider's brands for the vehicle kind.
    await user.click(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') }))
    expect(await screen.findByRole('option', { name: 'Audi' })).toBeInTheDocument()
  })

  it('a list that fails to load marks its codes and says so, never showing them as labels', async () => {
    installFakeBackend(
      catalogueRoutes([
        { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => status(500, { code: 'internal', message: 'boom' }) },
      ]),
    )

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })

    expect(await screen.findByText(i18n.t('catalogueBrowse.labelsLoadError.title'))).toBeInTheDocument()
    expect(screen.getByText('⚠ petrol')).toBeInTheDocument()
    expect(screen.queryByText('petrol')).not.toBeInTheDocument()
  })

  it('a brand list that fails to load says so on the brand field', async () => {
    installFakeBackend([
      { method: 'GET', match: /\/catalogue\/brands$/, handler: () => status(500, { code: 'internal', message: 'boom' }) },
      ...catalogueRoutes([]),
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />)

    const brand = screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') })
    await waitFor(() =>
      expect(brand).toHaveAccessibleDescription(i18n.t('catalogueBrowse.drilldown.brandsLoadError')),
    )
  })
})

// KAN-458: the catalogue offered «ist nicht» and «gleich» and never sent
// them, and answered «grösser als» with an inclusive bound. It now offers
// «gleich» / «mindestens» / «höchstens», sends each, and drops — neither
// sends nor shows — a predicate it cannot send.
describe('CatalogueBrowseGrid filters are sent as offered (KAN-458)', () => {
  function backend() {
    return installFakeBackend(catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]))
  }
  const route = (predicates: object[]) => `${SCOPED}&filters=${encodeURIComponent(JSON.stringify(predicates))}`

  it('«kW mindestens 110» and «Türen = 5» reach /catalogue/variants as kwMin and doorsMin/doorsMax, with the drill-down scope', async () => {
    const fake = backend()
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, {
      route: route([
        { id: 'a', fieldId: 'kw', type: 'number', condition: 'atLeast', value: 110 },
        { id: 'b', fieldId: 'doors', type: 'number', condition: 'equals', value: 5 },
      ]),
    })

    await screen.findByText('Golf GTI')
    const call = fake.callsTo(/\/catalogue\/variants$/, 'GET').at(-1)!
    expect(call.params.get('kwMin')).toBe('110')
    expect(call.params.get('doorsMin')).toBe('5')
    expect(call.params.get('doorsMax')).toBe('5')
    expect(call.params.get('brand')).toBe('Volkswagen')
    expect(call.params.get('vehicleKind')).toBe('passenger_car')
  })

  it('a saved «grösser als» is read as the «mindestens» it was always sent as', async () => {
    const fake = backend()
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, {
      route: route([{ id: 'a', fieldId: 'kw', type: 'number', condition: 'greaterThan', value: 110 }]),
    })

    await screen.findByText('Golf GTI')
    expect(fake.callsTo(/\/catalogue\/variants$/, 'GET').at(-1)!.params.get('kwMin')).toBe('110')
  })

  it('a second filter on the same field is neither sent nor counted — one filter per field', async () => {
    const fake = backend()
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, {
      route: route([
        { id: 'a', fieldId: 'kw', type: 'number', condition: 'atLeast', value: 110 },
        { id: 'b', fieldId: 'kw', type: 'number', condition: 'atMost', value: 200 },
      ]),
    })

    await screen.findByText('Golf GTI')
    const call = fake.callsTo(/\/catalogue\/variants$/, 'GET').at(-1)!
    expect(call.params.get('kwMin')).toBe('110')
    expect(call.params.get('kwMax')).toBeNull()
    const viewControl = screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) })
    expect(viewControl.textContent).toMatch(/(^|\D)1(\D|$)/)
  })

  // KAN-475: a saved view this grid applies in full shows as applied — its
  // legacy «grösser als» and a coded filter whose facets have not loaded
  // included — and only a view the grid narrows carries the dot.
  it('marks a saved view as changed only when the grid drops part of it', async () => {
    const user = userEvent.setup()
    const views = [
      {
        id: 'v1',
        name: 'Starke Diesel',
        snapshot: {
          filters: [
            { id: 'a', fieldId: 'kw', type: 'number', condition: 'greaterThan', value: 110 },
            { id: 'b', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'diesel' },
          ],
        },
      },
      {
        id: 'v2',
        name: 'Ohne Diesel',
        // «ist nicht» is no condition the live catalogue sends (the identAPI has none, KAN-548).
        snapshot: { filters: [{ id: 'c', fieldId: 'fuelType', type: 'select', condition: 'isNot', value: 'diesel' }] },
      },
    ]
    installFakeBackend([
      { method: 'GET', match: /\/me\/preferences\/views:mdm\.catalogue\.variants$/, handler: () => ({ payload: { schemaVersion: 1, views } }) },
      ...catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]),
    ])
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })
    await screen.findByText('Golf GTI')
    const changedDot = () => screen.queryByRole('img', { name: i18n.t('common.viewsAndFilters.viewChanged') })

    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) }))
    await user.click(await screen.findByText('Starke Diesel'))
    await screen.findByRole('button', { name: /Starke Diesel/ })
    expect(changedDot()).toBeNull()

    // The panel stays open after a view is applied; the next one is a click away.
    await user.click(await screen.findByText('Ohne Diesel'))
    await screen.findByRole('button', { name: /Ohne Diesel/ })
    expect(changedDot()).not.toBeNull()
  })

  it('a coded «ist» is sent as the field and counted as an active filter', async () => {
    const fake = backend()
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, {
      route: route([{ id: 'a', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'diesel' }]),
    })

    await screen.findByText('Golf GTI')
    const sent = fake.callsTo(/\/catalogue\/variants$/, 'GET').at(-1)!.params
    expect(sent.get('fuelType')).toBe('diesel')
    expect(sent.get('fuelTypeNot')).toBeNull()
    const viewControl = screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) })
    expect(viewControl.textContent).toMatch(/1/)
  })
})

// KAN-548 (ADR-112): the catalogue is a live provider query — nothing is
// asked before a brand is chosen, each page is the provider's, and the
// provider's states are alerts, never a broken grid.
describe('CatalogueBrowseGrid is a live, bounded provider query (KAN-548)', () => {
  it('asks for no variants and no facets before a brand is chosen, and says so', async () => {
    const fake = installFakeBackend(catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]))

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: '/catalogue' })

    expect(await screen.findByText(i18n.t('catalogueBrowse.needsBrand.title'))).toBeInTheDocument()
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/brands$/).length).toBe(1))
    expect(fake.callsTo(/\/catalogue\/variants$/)).toHaveLength(0)
    expect(fake.callsTo(/\/catalogue\/facets$/)).toHaveLength(0)
    expect(fake.callsTo(/\/catalogue\/model-groups$/)).toHaveLength(0)
  })

  it('with a brand, the page and the model groups are asked once each, with the scope; the facets only once the filter control opens', async () => {
    const user = userEvent.setup()
    const fake = installFakeBackend(catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]))

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: `${SCOPED}&kind=light_commercial&modelGroup=104` })

    await screen.findByText('Golf GTI')
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/model-groups$/).length).toBe(1))
    expect(fake.callsTo(/\/catalogue\/facets$/)).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) }))
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/facets$/).length).toBe(1))
    const variants = fake.callsTo(/\/catalogue\/variants$/, 'GET')
    expect(variants).toHaveLength(1)
    expect(variants[0].params.get('vehicleKind')).toBe('light_commercial')
    expect(variants[0].params.get('brand')).toBe('Volkswagen')
    expect(variants[0].params.get('modelGroupKey')).toBe('104')
    // the mode's production-year default narrows the model-group list, never a page (the identAPI
    // filters no production end)
    expect(variants[0].params.get('mode')).toBeNull()
    expect(fake.callsTo(/\/catalogue\/facets$/)[0].params.get('brand')).toBe('Volkswagen')
    expect(fake.callsTo(/\/catalogue\/model-groups$/)).toHaveLength(1)
    expect(fake.callsTo(/\/catalogue\/model-groups$/)[0].params.get('mode')).toBe('build')
  })

  it('fetches the next provider page only after the user moved in the grid, never by itself (ADR-112 (4))', async () => {
    const pages: Record<string, unknown[]> = {
      '': Array.from({ length: 10 }, (_, i) => ({ ...VARIANT, id: `p1-${i}`, variantName: `Golf ${i}` })),
      '2': Array.from({ length: 10 }, (_, i) => ({ ...VARIANT, id: `p2-${i}`, variantName: `Golf 1${i}` })),
      '3': Array.from({ length: 7 }, (_, i) => ({ ...VARIANT, id: `p3-${i}`, variantName: `Golf 2${i}` })),
    }
    // first match wins: the paged variants route goes before the defaults
    const fake = installFakeBackend([
      {
        method: 'GET',
        match: /\/catalogue\/variants$/,
        handler: (req) => {
          const cursor = req.params.get('cursor') ?? ''
          const next = cursor === '' ? '2' : cursor === '2' ? '3' : null
          return { items: pages[cursor], nextCursor: next, total: 27, totalIsEstimate: false, browseAvailable: true, state: 'ok' }
        },
      },
      ...catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]),
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })

    await screen.findByText('Golf 0')
    // ten rows fit the virtualizer's window, so the kit's prefetch rule would fire at once — it must not
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(fake.callsTo(/\/catalogue\/variants$/, 'GET')).toHaveLength(1)

    fireEvent.wheel(screen.getByText('Golf 0'), { deltaY: 300 })
    await screen.findByText('Golf 10')
    expect(fake.callsTo(/\/catalogue\/variants$/, 'GET')).toHaveLength(2)
    expect(fake.callsTo(/\/catalogue\/variants$/, 'GET')[1].params.get('cursor')).toBe('2')
    // one movement, one page: the third page waits for the next movement
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(fake.callsTo(/\/catalogue\/variants$/, 'GET')).toHaveLength(2)
  })

  it('the facets of the next brand wait for the filter control to be opened again', async () => {
    const user = userEvent.setup()
    const fake = installFakeBackend(catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]))

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })
    await screen.findByText('Golf GTI')
    const control = () => screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) })
    control().focus()
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(fake.callsTo(/\/catalogue\/facets$/)).toHaveLength(0)
    await user.click(control())
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/facets$/).length).toBe(1))
    await user.keyboard('{Escape}')

    await user.click(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') }))
    await user.click(await screen.findByRole('option', { name: 'Audi' }))
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/variants$/).some((c) => c.params.get('brand') === 'Audi')).toBe(true))
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(fake.callsTo(/\/catalogue\/facets$/).map((c) => c.params.get('brand'))).toEqual(['Volkswagen'])

    await user.click(control())
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/facets$/).map((c) => c.params.get('brand'))).toEqual(['Volkswagen', 'Audi']))
  })

  it('coming back to a scope whose filters were opened earlier costs no facet request until the control is clicked again', async () => {
    // the test client keeps no cache (gcTime 0), so a query that re-enables refetches at once — the
    // very refetch a stale production cache would make after five minutes
    const user = userEvent.setup()
    const fake = installFakeBackend(catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]))

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })
    await screen.findByText('Golf GTI')
    const control = () => screen.getByRole('button', { name: new RegExp(i18n.t('catalogueBrowse.allVariantsView')) })
    await user.click(control())
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/facets$/).length).toBe(1))
    await user.keyboard('{Escape}')

    const pickBrand = async (name: string) => {
      await user.click(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') }))
      await user.click(await screen.findByRole('option', { name }))
      await waitFor(() => expect(fake.callsTo(/\/catalogue\/variants$/).some((c) => c.params.get('brand') === name)).toBe(true))
    }
    await pickBrand('Audi')
    await pickBrand('Volkswagen')
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(fake.callsTo(/\/catalogue\/facets$/).map((c) => c.params.get('brand'))).toEqual(['Volkswagen'])

    await user.click(control())
    await waitFor(() => expect(fake.callsTo(/\/catalogue\/facets$/).map((c) => c.params.get('brand'))).toEqual(['Volkswagen', 'Volkswagen']))
  })

  it('a movement in one brand does not fetch the next page of the brand chosen after it', async () => {
    const user = userEvent.setup()
    const fake = installFakeBackend([
      {
        method: 'GET',
        match: /\/catalogue\/variants$/,
        handler: (req) =>
          req.params.get('brand') === 'Audi'
            ? { items: Array.from({ length: 10 }, (_, i) => ({ ...VARIANT, id: `a-${req.params.get('cursor') ?? '1'}-${i}`, variantName: `A3 p${req.params.get('cursor') ?? '1'} ${i}` })), nextCursor: req.params.get('cursor') ? null : '2', total: 12, totalIsEstimate: false, browseAvailable: true, state: 'ok' }
            : PAGE(),
      },
      ...catalogueRoutes([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }]),
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })
    await screen.findByText('Golf GTI')
    fireEvent.wheel(screen.getByText('Golf GTI'), { deltaY: 300 }) // a movement that loads nothing: Volkswagen has one page

    await user.click(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.drilldown.brand') }))
    await user.click(await screen.findByRole('option', { name: 'Audi' }))
    await screen.findByText('A3 p1 0')
    await new Promise((resolve) => setTimeout(resolve, 150))
    const audi = fake.callsTo(/\/catalogue\/variants$/, 'GET').filter((c) => c.params.get('brand') === 'Audi')
    expect(audi.map((c) => c.params.get('cursor'))).toEqual([null])
  })

  it.each([
    ['notConfigured', 'catalogueBrowse.notConfigured.title'],
    ['notInContract', 'catalogueBrowse.notInContract.title'],
    ['unavailable', 'catalogueBrowse.unavailable.title'],
  ])('the provider state %s is an alert, never a broken grid', async (state, titleKey) => {
    installFakeBackend([
      { method: 'GET', match: /\/catalogue\/brands$/, handler: () => ({ items: [], state }) },
      { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [], state }) },
      { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: false, state, coded: {}, numeric: {} }) },
      { method: 'GET', match: /\/catalogue\/variants$/, handler: () => ({ items: [], nextCursor: null, total: null, totalIsEstimate: false, browseAvailable: false, state }) },
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])

    renderWithProviders(<CatalogueBrowseGrid mode="build" />, { route: SCOPED })

    expect(await screen.findByTestId(`catalogue-state-${state}`)).toHaveTextContent(i18n.t(titleKey))
    expect(screen.queryByText(i18n.t('catalogueBrowse.loadError'))).not.toBeInTheDocument()
  })
})
