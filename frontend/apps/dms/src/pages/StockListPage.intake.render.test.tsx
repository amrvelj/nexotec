// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { CODED_REFERENCE_ROUTES } from '../test/configuratorFixtures'
import { StockListPage } from './StockListPage'

// KAN-463 — the Stock list has one intake, «Fahrzeug aufnehmen», in its
// header and in its empty state, and both open the configurator; the
// hand-typed form is gone. KAN-430 — a role that may write neither a stock
// item nor a configuration is not offered it.

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

const ITEM = {
  id: 'si-1', stockNumber: 'S-000001', vehicleLabel: 'VW Golf 8 GTI', vin: null, vehicleId: null, condition: 'used',
  lifecycleStatus: 'pipeline', reservationState: 'none', ageingBucket: null, odometerKm: null, listPrice: null,
  effectivePrice: null, updatedAt: '2026-10-30T00:00:00Z', createdAt: '2026-10-30T00:00:00Z', version: 1,
}

const CONFIGURATOR: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  ...CODED_REFERENCE_ROUTES,
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
]

function install({ items, writableCapabilities }: { items: unknown[]; writableCapabilities?: string[] }) {
  const session: FakeRoute[] = writableCapabilities
    ? [
        {
          method: 'GET',
          match: /\/auth\/me$/,
          handler: () => ({
            user: { id: 'u-1', firstName: 'Fritz', lastName: 'Finanz', email: 'f@example.ch', accessRoles: ['finance'] },
            activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
            memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
            entitlements: ALL_ENTITLEMENTS,
            writableCapabilities,
          }),
        },
      ]
    : []
  installFakeBackend([
    ...session,
    {
      method: 'GET',
      match: /^\/inventory\/stock-items$/,
      handler: () => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false }),
    },
    ...CONFIGURATOR,
  ])
}

const intake = () => i18n.t('stockList.newStockItem')

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('Stock list — one intake (KAN-463)', () => {
  it('offers one «Fahrzeug aufnehmen» in the header and nothing hand-typed', async () => {
    install({ items: [ITEM] })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText('VW Golf 8 GTI')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: intake() })).toHaveLength(1)
    expect(screen.queryByRole('link', { name: intake() })).not.toBeInTheDocument()
  })

  it('opens the configurator, with both modes, from the empty state', async () => {
    const user = userEvent.setup()
    install({ items: [] })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    const [, emptyStateAction] = await screen.findAllByRole('button', { name: intake() })
    await user.click(emptyStateAction)

    const overlay = await screen.findByRole('dialog')
    expect(within(overlay).getByText(i18n.t('configurator.mode.build'))).toBeInTheDocument()
    expect(within(overlay).getByText(i18n.t('configurator.mode.record'))).toBeInTheDocument()
  })

  it('offers no intake to a role that may write neither a stock item nor a configuration (KAN-430)', async () => {
    install({ items: [], writableCapabilities: ['customers'] })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText(i18n.t('stockList.emptyState.title'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: intake() })).not.toBeInTheDocument()
  })

  // KAN-511: the intake writes both rows, so either one alone is not enough.
  it.each([
    ['only stock items', ['stock_items']],
    ['only configurations', ['configurations']],
  ])('offers no intake to a role that may write %s', async (_, writableCapabilities) => {
    install({ items: [ITEM], writableCapabilities })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText('VW Golf 8 GTI')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: intake() })).not.toBeInTheDocument()
  })

  it('tells a reader where the cars come from instead of asking it to add one (KAN-511)', async () => {
    install({ items: [], writableCapabilities: ['stock_items'] })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText(i18n.t('stockList.emptyState.descriptionReadOnly'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockList.emptyState.description'))).not.toBeInTheDocument()
  })

  it('asks a writer to add the first car', async () => {
    install({ items: [] })
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText(i18n.t('stockList.emptyState.description'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockList.emptyState.descriptionReadOnly'))).not.toBeInTheDocument()
  })
})
