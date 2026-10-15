// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { translatedStockLifecycleLabel } from '../stockOptions'
import type { StockItemGroupPage, StockItemGroupRead, StockItemPage } from '../api/types'
import { StockListPage } from './StockListPage'

// KAN-357 — the group stock grid had no Columns control (FR-UI-02). Without
// a layout the kit's DataGrid rendered all eleven columns, its documented
// default subset ignored, and none could be hidden or reordered. It now has
// the column chooser, persisted per user under its own grid key: the four
// columns hidden by default (lifecycle status, odometer, first registration,
// VIN) are one click away (ADR-060).

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

const ROW: StockItemGroupRead = {
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
}

const GROUP_PAGE: StockItemGroupPage = { items: [ROW], nextCursor: null, total: 1, totalIsEstimate: false }
const EMPTY_OWN_PAGE: StockItemPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

const HIDDEN = [
  { header: () => i18n.t('stockList.columns.lifecycleStatus'), value: () => translatedStockLifecycleLabel(i18n.t, 'in_stock') },
  { header: () => i18n.t('stockList.columns.odometerKm'), value: () => "42'000" },
  { header: () => i18n.t('stockDetail.spec.firstRegistrationDate'), value: () => '01.03.2021' },
  { header: () => i18n.t('stockList.columns.vin'), value: () => 'WVWZZZ1KZAW000001' },
]

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

describe('StockListPage — the group grid has a column chooser (KAN-357)', () => {
  it('each hidden group column can be shown and renders its value; the layout is saved under its own key', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => GROUP_PAGE },
      { match: /^\/inventory\/stock-items$/, handler: () => EMPTY_OWN_PAGE },
    ])
    renderWithProviders(<StockListPage />, { route: '/stock?scope=group' })
    await screen.findByText('Garage Süd AG')

    for (const column of HIDDEN) {
      expect(screen.queryByRole('columnheader', { name: new RegExp(`^${escape(column.header())}`, 'i') })).toBeNull()
    }

    await user.click(screen.getByRole('button', { name: 'Columns' }))
    for (const column of HIDDEN) {
      await user.click(screen.getByRole('checkbox', { name: new RegExp(`^${escape(column.header())}$`, 'i') }))
    }

    const row = screen.getByRole('row', { name: /S-00042/ })
    for (const column of HIDDEN) {
      const headers = screen.getAllByRole('columnheader')
      const index = headers.findIndex((th) => (th.textContent ?? '').startsWith(column.header()))
      expect(index, column.header()).toBeGreaterThan(-1)
      expect(within(row).getAllByRole('cell')[index]).toHaveTextContent(column.value())
    }

    await waitFor(() => expect(backend.callsTo(/\/me\/preferences\/grid:inventory\.stock\.group$/, 'PUT').length).toBeGreaterThan(0))
    expect(backend.callsTo(/\/me\/preferences\/grid:inventory\.stock\.list$/, 'PUT')).toHaveLength(0)
  }, 20000)
})
