// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemPage } from '../api/types'
import { StockListPage } from './StockListPage'

// KAN-458: «Zustand = Neu» was offered, shown as one active filter, and never
// sent — the grid still listed used cars. The grid now sends it, and a
// predicate it does not offer (a hand-edited link, an old saved view) is
// neither sent nor counted as active.

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

const EMPTY_PAGE: StockItemPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

function routeWith(predicates: object[]): string {
  return `/stock?filters=${encodeURIComponent(JSON.stringify(predicates))}`
}

async function viewControl(): Promise<HTMLElement> {
  return screen.findByRole('button', { name: new RegExp(i18n.t('stockList.allStockView')) })
}

describe('StockListPage — every filter it shows is sent (KAN-458)', () => {
  it('«Zustand = Neu» in the URL is sent as condition=new and counted as one active filter', async () => {
    const backend = installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => EMPTY_PAGE }])

    renderWithProviders(<StockListPage />, {
      route: routeWith([{ id: 'p1', fieldId: 'condition', type: 'select', condition: 'is', value: 'new' }]),
    })

    await waitFor(() => expect(backend.callsTo(/^\/inventory\/stock-items$/, 'GET').length).toBeGreaterThan(0))
    const call = backend.callsTo(/^\/inventory\/stock-items$/, 'GET').at(-1)!
    expect(call.params.get('condition')).toBe('new')
    expect((await viewControl()).textContent).toMatch(/(^|\D)1(\D|$)/)
  })

  it('combines with the lifecycle filter', async () => {
    const backend = installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => EMPTY_PAGE }])

    renderWithProviders(<StockListPage />, {
      route: routeWith([
        { id: 'p1', fieldId: 'condition', type: 'select', condition: 'is', value: 'used' },
        { id: 'p2', fieldId: 'lifecycleStatus', type: 'select', condition: 'is', value: 'in_stock' },
      ]),
    })

    await waitFor(() => expect(backend.callsTo(/^\/inventory\/stock-items$/, 'GET').length).toBeGreaterThan(0))
    const call = backend.callsTo(/^\/inventory\/stock-items$/, 'GET').at(-1)!
    expect(call.params.get('condition')).toBe('used')
    expect(call.params.get('lifecycle_status')).toBe('in_stock')
  })

  it('a predicate the grid does not offer — «is not», an unknown field — is neither sent nor shown as active', async () => {
    const backend = installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => EMPTY_PAGE }])

    renderWithProviders(<StockListPage />, {
      route: routeWith([
        { id: 'p1', fieldId: 'condition', type: 'select', condition: 'isNot', value: 'used' },
        { id: 'p2', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' },
      ]),
    })

    await waitFor(() => expect(backend.callsTo(/^\/inventory\/stock-items$/, 'GET').length).toBeGreaterThan(0))
    const call = backend.callsTo(/^\/inventory\/stock-items$/, 'GET').at(-1)!
    expect(call.params.get('condition')).toBeNull()
    expect(call.params.get('fuelType')).toBeNull()
    expect((await viewControl()).textContent).not.toMatch(/\d/)
  })
})
