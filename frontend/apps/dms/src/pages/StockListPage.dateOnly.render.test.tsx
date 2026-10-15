// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemGroupRead, StockItemPage, StockItemRead } from '../api/types'
import { StockListPage } from './StockListPage'
import { buildStockGroupColumns } from './stock/columns/stockGroupColumns'

// jsdom has no layout: render every row (the shim the columns tests use).
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

const NOW = '2026-10-01T09:00:00Z'
const ITEM: StockItemRead = {
  id: 's1', stockNumber: 'ST-3330', vehicleLabel: 'VW Golf GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: null,
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  odometerKm: 12500, listPrice: '42500.00', effectivePrice: '41900.50', basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: '2026-08-03',
  expectedDelivery: '2026-12-01', pipelineRef: null, purchaseDate: '2026-09-15', purchaseInvoiceRef: null,
  purchasePrice: null, supplierIsVatRegistered: null, supplierName: null, valuationRefAmount: null,
  valuationRefId: null, valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW,
  version: 1,
}
const GROUP_ROW: StockItemGroupRead = {
  id: 'g-1', dealershipId: 'd-2', dealershipLabel: 'Garage Süd AG', stockNumber: 'S-00042',
  vin: 'WVWZZZ1KZAW000001', vehicleLabel: 'VW Golf 1.5 TSI', lifecycleStatus: 'in_stock', reservationState: 'none',
  condition: 'used', odometerKm: 42000, listPrice: '19900.00', firstRegistrationDate: '2021-03-01', updatedAt: NOW,
}
const page = <T,>(items: T[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

async function showColumns(keys: string[]) {
  await userEvent.click(screen.getByRole('button', { name: 'Columns' }))
  for (const key of keys) {
    const label = i18n.t(`stockList.columns.${key}`)
    await userEvent.click(screen.getByRole('checkbox', { name: new RegExp(`^${label}$`, 'i') }))
  }
}

// KAN-333 — purchase, order, expected-delivery and first-registration dates
// are date-only values; formatDate rendered them a day early west of
// Greenwich. Both grids: own stock and the group projection (ADR-055).
describe('StockListPage — date-only columns keep their day west of Greenwich (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('own stock: purchase, order and expected-delivery dates render as the recorded day', async () => {
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: (): StockItemPage => page([ITEM]) }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('ST-3330')

    await showColumns(['purchaseDate', 'orderDate', 'expectedDelivery'])

    expect(screen.getByText('15.09.2026')).toBeInTheDocument()
    expect(screen.getByText('03.08.2026')).toBeInTheDocument()
    expect(screen.getByText('01.12.2026')).toBeInTheDocument()
    for (const shifted of ['14.09.2026', '02.08.2026', '30.11.2026']) {
      expect(screen.queryByText(shifted)).not.toBeInTheDocument()
    }
  })

  // The group grid's first-registration column is hidden by default; its
  // cell is checked directly here, and shown through the column chooser in
  // StockListPage.groupColumns.render.test.tsx (KAN-357).
  it('group stock: the first registration date cell renders the recorded day', () => {
    const column = buildStockGroupColumns(i18n.t, 'de-CH').find((c) => c.id === 'firstRegistrationDate')!
    const cell = column.cell as (ctx: { row: { original: StockItemGroupRead } }) => string
    expect(cell({ row: { original: GROUP_ROW } })).toBe('01.03.2021')
  })
})
