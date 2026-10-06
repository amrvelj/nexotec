// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemGroupPage, StockItemGroupRead, StockItemPage } from '../api/types'
import { StockListPage } from './StockListPage'

// KAN-152 — § ADR-056: "Search term, active filter chips, sort, scope and
// the open tab serialise into query parameters". The stock scope used to
// live in component state (a reload fell back to own stock), and the
// group grid sorted nothing and filtered search in the browser.

// jsdom has no layout, so the virtualizer would render no rows; render
// every row (the same shim CustomersListPage.columns.render.test.tsx uses).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

function groupRow(overrides: Partial<StockItemGroupRead> = {}): StockItemGroupRead {
  return {
    id: 'g-1',
    dealershipId: 'd-2',
    dealershipLabel: 'Garage Süd AG',
    stockNumber: 'S-00042',
    vin: 'WVWZZZ1KZAW000001',
    vehicleLabel: 'VW Golf 1.5 TSI',
    lifecycleStatus: 'in_stock',
    reservationState: 'none',
    condition: 'used',
    odometerKm: 42000,
    listPrice: '19900.00',
    firstRegistrationDate: '2021-03-01',
    updatedAt: '2026-10-01T08:00:00Z',
    ...overrides,
  }
}

function groupPage(items: StockItemGroupRead[], nextCursor: string | null = null): StockItemGroupPage {
  return { items, nextCursor, total: items.length, totalIsEstimate: false }
}

const EMPTY_OWN_PAGE: StockItemPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

function renderAt(route: string) {
  return renderWithProviders(
    <>
      <LocationProbe />
      <StockListPage />
    </>,
    { route },
  )
}

describe('StockListPage — scope is part of the URL (ADR-056, KAN-152)', () => {
  it('a pasted ?scope=group URL opens the group grid with its search and sort sent to the server', async () => {
    const backend = installFakeBackend([
      { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => groupPage([groupRow()]) },
      { match: /^\/inventory\/stock-items$/, handler: () => EMPTY_OWN_PAGE },
    ])

    renderAt('/stock?scope=group&q=Golf&sort=stockNumber:asc')

    // The group projection's own column carries the dealership label.
    expect(await screen.findByText('Garage Süd AG')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('stockList.scope.group') })).toBeInTheDocument()
    expect(screen.getByDisplayValue('Golf')).toBeInTheDocument()

    const stockNumberHeader = screen.getByRole('columnheader', {
      name: new RegExp(i18n.t('stockList.columns.stockNumber'), 'i'),
    })
    expect(stockNumberHeader).toHaveAttribute('aria-sort', 'ascending')

    const call = backend.callsTo(/^\/inventory\/groups\/mine\/stock-items$/, 'GET').at(-1)!
    expect(call.params.get('q')).toBe('Golf')
    expect(call.params.get('sort')).toBe('stockNumber:asc')
    expect(call.params.get('limit')).toBe('50')

    // Own stock is not fetched behind the group grid.
    expect(backend.callsTo(/^\/inventory\/stock-items$/, 'GET')).toHaveLength(0)
  })

  it('switching scope writes ?scope=group, and switching back removes it', async () => {
    installFakeBackend([
      { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => groupPage([groupRow()]) },
      { match: /^\/inventory\/stock-items$/, handler: () => EMPTY_OWN_PAGE },
    ])

    renderAt('/stock?q=Golf')

    await userEvent.click(await screen.findByRole('button', { name: i18n.t('stockList.scope.own') }))
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('stockList.scope.group') }))

    expect(await screen.findByText('Garage Süd AG')).toBeInTheDocument()
    const groupSearch = screen.getByTestId('location-search').textContent ?? ''
    expect(groupSearch).toContain('scope=group')
    // Search survives the scope switch.
    expect(groupSearch).toContain('q=Golf')

    await userEvent.click(screen.getByRole('button', { name: i18n.t('stockList.scope.group') }))
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('stockList.scope.own') }))

    await waitFor(() => expect(screen.getByTestId('location-search').textContent).not.toContain('scope='))
    expect(screen.queryByText('Garage Süd AG')).not.toBeInTheDocument()
  })

  it('entering group scope drops own-stock filters, which the group grid does not apply (ADR-058)', async () => {
    installFakeBackend([
      { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => groupPage([groupRow()]) },
      { match: /^\/inventory\/stock-items$/, handler: () => EMPTY_OWN_PAGE },
    ])
    const predicate = { id: 'p1', fieldId: 'lifecycleStatus', type: 'select', condition: 'is', value: 'in_stock' }

    renderAt(`/stock?filters=${encodeURIComponent(JSON.stringify([predicate]))}`)
    expect(screen.getByTestId('location-search').textContent).toContain('filters=')

    await userEvent.click(await screen.findByRole('button', { name: i18n.t('stockList.scope.own') }))
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('stockList.scope.group') }))

    await screen.findByText('Garage Süd AG')
    const search = screen.getByTestId('location-search').textContent ?? ''
    expect(search).toContain('scope=group')
    expect(search).not.toContain('filters=')
  })

  it('sorting the group grid is a server-side sort recorded in the URL', async () => {
    const backend = installFakeBackend([
      { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => groupPage([groupRow()]) },
    ])

    renderAt('/stock?scope=group')
    await screen.findByText('Garage Süd AG')

    const header = screen.getByRole('columnheader', { name: new RegExp(i18n.t('stockList.columns.stockNumber'), 'i') })
    await userEvent.click(within(header).getByText(i18n.t('stockList.columns.stockNumber')))

    await waitFor(() => expect(screen.getByTestId('location-search').textContent).toContain('sort=stockNumber'))
    await waitFor(() => {
      const call = backend.callsTo(/^\/inventory\/groups\/mine\/stock-items$/, 'GET').at(-1)!
      expect(call.params.get('sort')).toMatch(/^stockNumber:(asc|desc)$/)
    })
  })

  it('the group grid loads further pages by cursor and shows the server total', async () => {
    const backend = installFakeBackend([
      {
        match: /^\/inventory\/groups\/mine\/stock-items$/,
        handler: (req) =>
          req.params.get('cursor') === 'cursor-2'
            ? { ...groupPage([groupRow({ id: 'g-2', stockNumber: 'S-00043', dealershipLabel: 'Garage Nord AG' })]), total: 120 }
            : { ...groupPage([groupRow()], 'cursor-2'), total: 120 },
      },
    ])

    renderAt('/stock?scope=group')

    // The second page arrives through the grid's load-more, by cursor.
    expect(await screen.findByText('Garage Nord AG')).toBeInTheDocument()
    expect(screen.getByText('Garage Süd AG')).toBeInTheDocument()
    const cursors = backend.callsTo(/^\/inventory\/groups\/mine\/stock-items$/, 'GET').map((c) => c.params.get('cursor'))
    expect(cursors).toEqual([null, 'cursor-2'])

    // The footer counts against the server's total, not the loaded rows.
    expect(screen.getByText(i18n.t('common.showingOfTotal', { count: 2, total: '120' }))).toBeInTheDocument()
  })
})
