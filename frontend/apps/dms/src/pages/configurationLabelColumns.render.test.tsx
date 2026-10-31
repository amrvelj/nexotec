// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { StockItemRead, ValuationRead } from '../api/types'
import { StockListPage } from './StockListPage'
import { ValuationsListPage } from './ValuationsListPage'

// KAN-190 item 1 (ADR-060: every persisted field is available as a grid
// column) — KAN-10 persisted `configurationLabel` on the stock item and the
// valuation (rule 2's copy of the configuration's label), and neither grid
// offered it. Both now do, hidden by default, one click away in the
// Columns panel. Not sortable: the column has no index (U-02/U-03).

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
const LABEL = 'Škoda Octavia Combi 2.0 TDI'
const page = <T,>(items: T[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

const STOCK_ITEM = {
  id: 's1', stockNumber: 'ST-0190', vehicleLabel: 'Škoda Octavia', vin: null, vehicleId: null, condition: 'new',
  lifecycleStatus: 'pipeline', reservationState: 'none', ageingBucket: null, odometerKm: null, listPrice: null,
  effectivePrice: null, configurationId: 'cfg-1', configurationLabel: LABEL, createdAt: NOW, updatedAt: NOW, version: 1,
} as unknown as StockItemRead
const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000190', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: null, customerLabel: null, vehicleId: null, vehicleMake: 'Škoda', vehicleModel: 'Octavia', vehicleTrim: null,
  vehicleVin: null, finalOffer: '12000.00', deductions: [], version: 1, validFrom: '2026-10-01',
  validUntil: '2026-10-31', configurationId: 'cfg-1', configurationLabel: LABEL, createdAt: NOW, updatedAt: NOW,
} as unknown as ValuationRead

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('The configuration is a grid column (KAN-190, ADR-060)', () => {
  it.each([
    ['Stock', () => <StockListPage />, '/stock', { match: /^\/inventory\/stock-items$/, handler: () => page([STOCK_ITEM]) }, 'ST-0190', 'stockList.columns.configuration'],
    ['Valuations', () => <ValuationsListPage />, '/valuations', { match: /^\/valuations$/, handler: () => page([VALUATION]) }, 'B-000190', 'valuationsList.columns.configuration'],
  ] as const)('%s: hidden by default, and the Columns panel shows the label', async (_name, ui, route, backendRoute, rowText, headerKey) => {
    const user = userEvent.setup()
    installFakeBackend([backendRoute as FakeRoute])
    renderWithProviders(ui(), { route })
    await screen.findByText(rowText)
    const header = i18n.t(headerKey)
    const headers = () => screen.getAllByRole('columnheader').map((th) => th.textContent ?? '')

    expect(headers().some((text) => text.startsWith(header))).toBe(false)
    expect(screen.queryByText(LABEL)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
    const list = await screen.findByRole('list')
    await user.click(within(list).getByRole('checkbox', { name: new RegExp(`^${header}$`, 'i') }))

    expect(headers().some((text) => text.startsWith(header))).toBe(true)
    expect(within(screen.getByRole('row', { name: new RegExp(rowText) })).getByText(LABEL)).toBeInTheDocument()
  })
})
