// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { GroupStockDealershipList, StockItemGroupPage, StockItemPage } from '../api/types'
import { StockListPage } from './StockListPage'

// KAN-565 — ADR-055 says group stock is «a scope switch with the dealership
// as a filter», and the group grid had no filter at all: «which of the
// sister Betrieb's cars matches this customer» could not be narrowed to one
// Betrieb. It now offers «Betrieb ist / ist nicht», its options the group's
// roster, its predicate in the URL beside ?scope=group.

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

const EMPTY_GROUP_PAGE: StockItemGroupPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const EMPTY_OWN_PAGE: StockItemPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const ROSTER: GroupStockDealershipList = {
  items: [
    { id: 'd-1', label: 'Garage Nord AG' },
    { id: 'd-2', label: 'Garage Süd AG' },
  ],
}

function backend() {
  return installFakeBackend([
    { match: /^\/inventory\/groups\/mine\/dealerships$/, handler: () => ROSTER },
    { match: /^\/inventory\/groups\/mine\/stock-items$/, handler: () => EMPTY_GROUP_PAGE },
    { match: /^\/inventory\/stock-items$/, handler: () => EMPTY_OWN_PAGE },
  ])
}

function groupRouteWith(predicates: object[]): string {
  return `/stock?scope=group&filters=${encodeURIComponent(JSON.stringify(predicates))}`
}

async function viewControl(): Promise<HTMLElement> {
  return screen.findByRole('button', { name: new RegExp(i18n.t('stockList.allGroupStockView')) })
}

const lastListingCall = (fake: ReturnType<typeof backend>) =>
  fake.callsTo(/^\/inventory\/groups\/mine\/stock-items$/, 'GET').at(-1)!

describe('StockListPage — group stock filtered by Betrieb (KAN-565)', () => {
  it('«Betrieb ist Garage Süd AG» in the URL is sent as dealershipId and counted as one active filter', async () => {
    const fake = backend()
    renderWithProviders(<StockListPage />, {
      route: groupRouteWith([{ id: 'p1', fieldId: 'dealershipId', type: 'select', condition: 'is', value: 'd-2' }]),
    })

    await waitFor(() => expect(lastListingCall(fake)?.params.getAll('dealershipId')).toEqual(['d-2']))
    expect(lastListingCall(fake).params.get('dealershipIdNot')).toBeNull()
    expect((await viewControl()).textContent).toMatch(/(^|\D)1(\D|$)/)
    // The options are the group's roster, read from its own endpoint.
    expect(fake.callsTo(/^\/inventory\/groups\/mine\/dealerships$/, 'GET').length).toBeGreaterThan(0)
  })

  it('«ist nicht» is sent as dealershipIdNot', async () => {
    const fake = backend()
    renderWithProviders(<StockListPage />, {
      route: groupRouteWith([{ id: 'p1', fieldId: 'dealershipId', type: 'select', condition: 'isNot', value: 'd-1' }]),
    })

    await waitFor(() => expect(lastListingCall(fake)?.params.getAll('dealershipIdNot')).toEqual(['d-1']))
    expect(lastListingCall(fake).params.get('dealershipId')).toBeNull()
  })

  it('an own-stock predicate in a group URL is neither sent nor shown (ADR-102)', async () => {
    const fake = backend()
    renderWithProviders(<StockListPage />, {
      route: groupRouteWith([{ id: 'p1', fieldId: 'condition', type: 'select', condition: 'is', value: 'new' }]),
    })

    await waitFor(() => expect(lastListingCall(fake)).toBeDefined())
    const sent = lastListingCall(fake).params
    for (const name of ['condition', 'dealershipId', 'dealershipIdNot']) expect(sent.get(name)).toBeNull()
    expect((await viewControl()).textContent).not.toMatch(/\d/)
  })

  it('a Betrieb picked in the filter builder shows its name and is sent', async () => {
    const user = userEvent.setup()
    const fake = backend()
    renderWithProviders(<StockListPage />, { route: '/stock?scope=group' })

    await waitFor(() => expect(fake.callsTo(/^\/inventory\/groups\/mine\/dealerships$/, 'GET').length).toBeGreaterThan(0))
    await user.click(await viewControl())
    await user.click(await screen.findByRole('button', { name: i18n.t('common.filterBuilder.addFilter') }))
    const combos = screen.getAllByRole('combobox')
    // The one field is the Betrieb, «ist» or «ist nicht».
    expect(within(combos[0]).getAllByRole('option').map((o) => o.textContent)).toEqual([i18n.t('stockList.columns.dealership')])
    expect(within(combos[1]).getAllByRole('option').map((o) => o.textContent)).toEqual([
      i18n.t('common.filterBuilder.conditions.is'),
      i18n.t('common.filterBuilder.conditions.isNot'),
    ])
    expect(within(combos[2]).getAllByRole('option').map((o) => o.textContent)).toEqual(
      expect.arrayContaining(['Garage Nord AG', 'Garage Süd AG'])
    )
    await user.selectOptions(combos[2], 'Garage Süd AG')
    await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(lastListingCall(fake).params.getAll('dealershipId')).toEqual(['d-2']))
    expect(screen.getAllByText(/Garage Süd AG/).length).toBeGreaterThan(0)
  })
})
