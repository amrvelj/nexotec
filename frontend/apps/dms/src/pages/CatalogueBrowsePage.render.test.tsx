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

const BRAND = { id: 'brand-vw', code: 'vw', displayName: 'Volkswagen', version: 1, createdAt: '', updatedAt: '' }
const GROUP = { id: 'group-golf', brandId: 'brand-vw', name: 'Golf' }

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
    { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: [BRAND], nextCursor: null }) },
    {
      method: 'GET',
      match: /\/catalogue\/model-groups$/,
      handler: ({ params }) => ({ items: params.get('brandId') === 'brand-vw' ? [GROUP] : [] }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/facets$/,
      handler: () => ({
        browseAvailable: true,
        coded: { fuelType: [{ valueCode: 'petrol', count: 3 }] },
        numeric: { ps: { min: '120', max: '320' } },
      }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: ({ params }) => {
        const narrowed = params.get('modelGroup') === 'group-golf'
        const rows = narrowed ? [variant()] : [variant(), variant({ id: 'v2', variantName: 'Golf R' })]
        return { items: rows, nextCursor: null, total: rows.length, totalIsEstimate: false, browseAvailable: true }
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

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('brand=brand-vw'))
    await waitFor(() => expect(modelGroupInput()).not.toBeDisabled())

    await user.click(modelGroupInput())
    await user.click(await screen.findByRole('option', { name: 'Golf' }))

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('modelGroup=group-golf'))
    await waitFor(() => expect(ctx.variantRequests().at(-1)?.get('modelGroup')).toBe('group-golf'))
  })

  it('facet filter: a fuelType filter in the URL reaches /catalogue/variants', async () => {
    const ctx = install()
    const predicate = { id: 'p1', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' }
    renderWithProviders(<CatalogueBrowsePage />, {
      route: `/catalogue?filters=${encodeURIComponent(JSON.stringify([predicate]))}`,
    })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(ctx.variantRequests().length).toBeGreaterThan(0))
    expect(ctx.variantRequests().at(-1)?.get('fuelType')).toBe('petrol')
  })

  it('mode → production-year: build is the default, switching to record drops the in-production filter', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <>
        <LocationProbe />
        <CatalogueBrowsePage />
      </>,
      { route: '/catalogue' },
    )
    await screen.findByRole('columnheader', { name: variantHeader() })

    await waitFor(() => expect(ctx.variantRequests().at(-1)?.get('mode')).toBe('build'))

    await user.click(screen.getByRole('radio', { name: i18n.t('catalogueBrowse.mode.record') }))

    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('mode=record'))
    await waitFor(() => expect(ctx.variantRequests().at(-1)?.get('mode')).toBe('record'))
  })

  it('mode → production-year: a record-mode URL opens in record mode', async () => {
    const ctx = install()
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?mode=record' })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(ctx.variantRequests().at(-1)?.get('mode')).toBe('record'))
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
    const first = renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue' })
    expect(await screen.findByRole('columnheader', { name: psHeader() })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Columns' }))
    await user.click(await screen.findByRole('checkbox', { name: psHeader() }))

    await waitFor(() => expect(screen.queryByRole('columnheader', { name: psHeader() })).not.toBeInTheDocument())
    await waitFor(() => expect(backend.callsTo(GRID_PREF, 'PUT').length).toBeGreaterThan(0), { timeout: 2000 })

    first.unmount()
    window.localStorage.clear()
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue' })
    await screen.findByRole('columnheader', { name: variantHeader() })
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: psHeader() })).not.toBeInTheDocument())
  })
})
