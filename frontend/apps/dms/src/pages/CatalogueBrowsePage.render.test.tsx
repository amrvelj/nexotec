// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { useLocation } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeBackend, type FakeRoute } from '../test/fakeBackend'
import { CatalogueBrowsePage } from './CatalogueBrowsePage'

// Configurator C-B (KAN-40 / FR-C-01). Every UX criterion is proven here,
// not on a manual look (PR #61 harness).

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="loc">{location.search}</div>
}

// KAN-548: the drill-down is live on the provider — a brand list per vehicle
// kind, a brand's model groups (keyed by the provider's key), one page of
// variants once a brand is chosen; every answer carries a `state`.
const BRAND = { name: 'Volkswagen', brandNr: '2', models: ['Golf'] }
const GROUP = { key: 'group-golf', name: 'Golf', modelSearch: 'GOLF', productionYearFrom: 2019, productionYearTo: null }

function variant(over: Record<string, unknown> = {}) {
  return {
    id: 'v1',
    brandId: 'brand-vw',
    brandDisplayName: 'Volkswagen',
    modelGroupId: 'group-golf',
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
    spec: { ps: 245, displacementCcm: 1984, doors: 5, seats: 5, trimName: 'GTI' },
    updatedAt: '2026-01-01T00:00:00Z',
    ...over,
  }
}

interface Ctx {
  backend: FakeBackend
  variantRequests: () => URLSearchParams[]
}

function install(over: FakeRoute[] = []): Ctx {
  const routes: FakeRoute[] = [
    { method: 'GET', match: /\/catalogue\/brands$/, handler: () => ({ items: [BRAND], state: 'ok' }) },
    {
      method: 'GET',
      match: /\/catalogue\/model-groups$/,
      handler: ({ params }) => ({ items: params.get('brand') === 'Volkswagen' ? [GROUP] : [], state: 'ok' }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/facets$/,
      handler: () => ({ browseAvailable: true, state: 'ok', coded: { fuelType: [{ valueCode: 'petrol', count: null }] }, numeric: {} }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: ({ params }) => {
        const narrowed = params.get('modelGroupKey') === 'group-golf'
        const rows = narrowed ? [variant()] : [variant(), variant({ id: 'v2', variantName: 'Golf R' })]
        return { items: rows, nextCursor: null, total: rows.length, totalIsEstimate: false, browseAvailable: true, state: 'ok' }
      },
    },
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ...over,
  ]
  const backend = installFakeBackend(routes)
  return {
    backend,
    variantRequests: () => backend.callsTo(/\/catalogue\/variants$/, 'GET').map((c) => c.params),
  }
}

const variantHeader = () => new RegExp(i18n.t('catalogueBrowse.columns.variant'), 'i')

describe('CatalogueBrowsePage — FR-C-01', () => {
  it('drill-down: choosing a brand then a model group narrows the grid and lands in the URL', async () => {
    const user = userEvent.setup()
    const ctx = install()
    const { container } = renderWithProviders(
      <>
        <LocationProbe />
        <CatalogueBrowsePage />
      </>,
      { route: '/catalogue' },
    )
    await screen.findByRole('columnheader', { name: variantHeader() })

    const modelGroupInput = () =>
      container.querySelector<HTMLInputElement>(
        `input[aria-label="${i18n.t('catalogueBrowse.drilldown.modelGroup')}"]`,
      )!
    expect(modelGroupInput()).toBeDisabled()

    await user.click(
      container.querySelector<HTMLInputElement>(
        `input[aria-label="${i18n.t('catalogueBrowse.drilldown.brand')}"]`,
      )!,
    )
    await user.click(await screen.findByRole('option', { name: 'Volkswagen' }))

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('brand=Volkswagen'))
    await waitFor(() => expect(modelGroupInput()).not.toBeDisabled())
    // the first page is asked only now, with the brand (ADR-112 (4))
    await waitFor(() => expect(ctx.variantRequests().length).toBe(1))
    expect(ctx.variantRequests()[0].get('brand')).toBe('Volkswagen')

    await user.click(modelGroupInput())
    await user.click(await screen.findByRole('option', { name: 'Golf' }))

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('modelGroup=group-golf'))
    await waitFor(() => expect(ctx.variantRequests().at(-1)?.get('modelGroupKey')).toBe('group-golf'))
  })

  it('facet filter: a fuelType filter in the URL reaches /catalogue/variants', async () => {
    const ctx = install()
    const predicate = { id: 'p1', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' }
    renderWithProviders(<CatalogueBrowsePage />, {
      route: `/catalogue?brand=Volkswagen&filters=${encodeURIComponent(JSON.stringify([predicate]))}`,
    })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(ctx.variantRequests().length).toBeGreaterThan(0))
    expect(ctx.variantRequests().at(-1)?.get('fuelType')).toBe('petrol')
  })

  it('mode → production-year: build is the default and narrows the model-group list; record drops it (PRD NurNeue)', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <>
        <LocationProbe />
        <CatalogueBrowsePage />
      </>,
      { route: '/catalogue?brand=Volkswagen' },
    )
    await screen.findByRole('columnheader', { name: variantHeader() })
    const groupRequests = () => ctx.backend.callsTo(/\/catalogue\/model-groups$/, 'GET').map((c) => c.params)

    await waitFor(() => expect(groupRequests().at(-1)?.get('mode')).toBe('build'))
    // the identAPI's variant rows carry no filterable production end: a page is not narrowed by the mode
    expect(ctx.variantRequests().at(-1)?.get('mode')).toBeNull()

    await user.click(screen.getByRole('radio', { name: i18n.t('catalogueBrowse.mode.record') }))

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('mode=record'))
    await waitFor(() => expect(groupRequests().at(-1)?.get('mode')).toBe('record'))
  })

  it('mode → production-year: a record-mode URL opens in record mode', async () => {
    const ctx = install()
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?mode=record&brand=Volkswagen' })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(ctx.backend.callsTo(/\/catalogue\/model-groups$/, 'GET').at(-1)?.params.get('mode')).toBe('record'))
  })

  it('tabs: ?tab=mapping-gaps renders the mapping-gap queue', async () => {
    const user = userEvent.setup()
    install([
      { method: 'GET', match: /\/vehicle-mdm\/mapping-gaps$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(
      <>
        <LocationProbe />
        <CatalogueBrowsePage />
      </>,
      { route: '/catalogue' },
    )

    await user.click(await screen.findByRole('tab', { name: i18n.t('catalogueBrowse.tabs.mappingGaps') }))
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('tab=mapping-gaps'))
    expect(await screen.findByPlaceholderText(i18n.t('mappingGaps.searchPlaceholder'))).toBeInTheDocument()
  })

  it('grid contracts: a hidden column persists to /me/preferences and restores on remount', async () => {
    const user = userEvent.setup()
    let stored: unknown = {}
    const GRID_PREF = /\/me\/preferences\/grid:mdm\.catalogue\.variants$/
    const { backend } = install([
      { method: 'GET', match: GRID_PREF, handler: () => ({ payload: stored }) },
      {
        method: 'PUT',
        match: GRID_PREF,
        handler: (req) => {
          stored = req.body
          return { ok: true }
        },
      },
    ])

    const psHeader = () => new RegExp(`^${i18n.t('catalogueBrowse.columns.ps')}$`, 'i')
    const first = renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?brand=Volkswagen' })
    expect(await screen.findByRole('columnheader', { name: psHeader() })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
    await user.click(await screen.findByRole('checkbox', { name: psHeader() }))

    await waitFor(() => expect(screen.queryByRole('columnheader', { name: psHeader() })).not.toBeInTheDocument())
    await waitFor(() => expect(backend.callsTo(GRID_PREF, 'PUT').length).toBeGreaterThan(0), { timeout: 2000 })

    first.unmount()
    window.localStorage.clear()
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?brand=Volkswagen' })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: psHeader() })).not.toBeInTheDocument())
  })
})
