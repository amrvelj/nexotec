// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend, status } from '../../../test/fakeBackend'
import gridSchemas from '../../../api/gridSchemas.json'
import type { StockItemPage, StockItemRead } from '../../../api/types'
import { StockListPage } from './StockListPage'

// jsdom has no layout: render every row (the shim the other list tests use).
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
  landedCost: null, locationId: null, locationLabel: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null,
  expectedDelivery: null, pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null,
  purchasePrice: null, supplierIsVatRegistered: null, supplierContactId: null, supplierLabel: null, valuationRefAmount: null,
  valuationRefId: null, valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW,
  version: 1,
}

// KAN-859: the list waits for its served grid schema (KAN-461). A failed
// read of it was never read back, so the grid stayed on its skeleton for
// good. It is the list's load error now, and the retry reads the schema again.
describe('StockListPage — a failed grid-schema read (KAN-859)', () => {
  it('is said with a retry that reads the schema again, not an endless skeleton', async () => {
    let schemaUp = false
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /^\/inventory\/stock-items\/grid-schema$/,
        handler: () =>
          schemaUp
            ? (gridSchemas as Record<string, unknown>)['/v1/inventory/stock-items']
            : status(503, { error: { code: 'unavailable', message: 'down', details: null } }),
      },
      {
        method: 'GET',
        match: /^\/inventory\/stock-items$/,
        handler: (): StockItemPage => ({ items: [ITEM], nextCursor: null, total: 1, totalIsEstimate: false }),
      },
    ])
    renderWithProviders(<StockListPage />, { route: '/stock' })

    expect(await screen.findByText(i18n.t('stockList.loadError'))).toBeInTheDocument()
    expect(backend.callsTo(/^\/inventory\/stock-items$/, 'GET')).toHaveLength(0)

    schemaUp = true
    await userEvent.click(screen.getByRole('button', { name: i18n.t('common.retry') }))

    expect(await screen.findByText('ST-3330')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockList.loadError'))).not.toBeInTheDocument()
  })
})
