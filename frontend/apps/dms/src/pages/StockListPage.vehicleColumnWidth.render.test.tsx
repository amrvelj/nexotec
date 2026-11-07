// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemPage, StockItemRead } from '../api/types'
import { StockListPage } from './StockListPage'
import { buildStockGroupColumns } from './stock/columns/stockGroupColumns'
import { STOCK_VEHICLE_COLUMN_WIDTH } from './stock/columns/stockVehicleColumn'

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

const NOW = '2026-11-07T09:00:00Z'
const ITEM: StockItemRead = {
  id: 's1', stockNumber: 'S-000002', vehicleLabel: 'Škoda Octavia Combi 2.0 TDI RS 4x4', vin: 'TMBJJ7NX5MY012345',
  vehicleId: null, condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  odometerKm: 23000, listPrice: null, effectivePrice: null, basePrice: null, bodyStyle: null, exteriorColour: null,
  firstRegistrationDate: null, inStockAt: NOW, leftStockAt: null, isInvoiceable: false, landedCost: null,
  locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null, notionalInputTaxOverridden: false,
  notionalInputTaxRate: null, orderDate: null, expectedDelivery: null, pipelineRef: null, purchaseDate: null,
  purchaseInvoiceRef: null, purchasePrice: null, supplierIsVatRegistered: null, supplierName: null,
  valuationRefAmount: null, valuationRefId: null, valuationRefSource: null, valuationRefValuedAt: null,
  createdAt: NOW, updatedAt: NOW, version: 1,
}
const page = (items: StockItemRead[]): StockItemPage => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })
const GRID_STORAGE_KEY = 'dms.preferences.grid.inventory.stock.list'

function vehicleCell(): HTMLElement {
  return screen.getByText('Škoda Octavia Combi 2.0 TDI RS 4x4').closest('[role="cell"]') as HTMLElement
}

afterEach(() => window.localStorage.clear())

// KAN-578 (Anto, 2026-11-07, option A) — the «Fahrzeug» column used to get
// an even share of the row (`flex: 1 1 0`, ~140 px at 1440 px), so most car
// names ended in «…». It opens at a fixed default width now; the user's own
// width (the column panel's resize, stored per grid) still wins.
describe('Stock grids — the «Fahrzeug» column opens wide enough for a car name (KAN-578)', () => {
  it('own stock: the vehicle column has its default width, not an even share', async () => {
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => page([ITEM]) }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('S-000002')
    expect(STOCK_VEHICLE_COLUMN_WIDTH).toBeGreaterThanOrEqual(280)
    expect(vehicleCell().style.width).toBe(`${STOCK_VEHICLE_COLUMN_WIDTH}px`)
    expect(vehicleCell().style.flex).toBe(`0 0 ${STOCK_VEHICLE_COLUMN_WIDTH}px`)
  })

  it('own stock: a width the user set wins over the default', async () => {
    window.localStorage.setItem(
      GRID_STORAGE_KEY,
      JSON.stringify({ schemaVersion: 1, sort: [], columnLayout: { order: [], hidden: [], widths: { vehicleLabel: 180 }, pinnedLeft: [] } }),
    )
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => page([ITEM]) }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('S-000002')
    expect(vehicleCell().style.width).toBe('180px')
  })

  it('group stock: the same default width', () => {
    const column = buildStockGroupColumns(i18n.t, 'de-CH').find((c) => c.id === 'vehicleLabel')!
    expect(column.meta?.width).toBe(STOCK_VEHICLE_COLUMN_WIDTH)
  })
})
