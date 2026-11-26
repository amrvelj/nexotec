// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { Route, Routes, useParams } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend } from '../../../test/fakeBackend'
import { ServiceOrdersListPage } from './ServiceOrdersListPage'
import { summary } from '../test/serviceOrderFixtures'

// KAN-810 criterion 6 (D1): the workshop orders — a search and a state filter, both sent to
// the server (Anto, 2026-11-26: no chips yet), every row opening its order.

// jsdom has no layout: the virtualizer renders every row (as in the other list tests).
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

function OrderStandIn() {
  const { id } = useParams<{ id: string }>()
  return <p>order page {id}</p>
}

function install(writable = ALL_GATED_CAPABILITIES) {
  return installFakeBackend([
    {
      method: 'GET',
      match: /^\/service-orders$/,
      handler: () => ({
        items: [summary(), summary({ id: 'order-2', orderNumber: 'WA-2026-0407', state: 'cancelled', vehicleLabel: 'Audi Q5 · WAUZZZ8R0DA000001', contact: { contactId: 'c-2', label: 'Vreni Iseli' } })],
        nextCursor: null,
        total: 2,
        totalIsEstimate: false,
      }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: {
          id: 'user-1', dealershipId: 'dealership-1', firstName: 'Sandra', lastName: 'Keller', email: 'sk@example.ch', phone: null,
          role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false, employmentStatus: 'active',
          authIdentityId: 'auth-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
        activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
        memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
        entitlements: ALL_ENTITLEMENTS,
        writableCapabilities: writable,
      }),
    },
  ])
}

function renderList(route = '/aftersales') {
  renderWithProviders(
    <Routes>
      <Route path="/aftersales" element={<ServiceOrdersListPage />} />
      <Route path="/aftersales/orders/:id" element={<OrderStandIn />} />
    </Routes>,
    { route },
  )
}

describe('the workshop order list (KAN-810, D1)', () => {
  it('shows each order: number, vehicle · ordering party, state, date and location', async () => {
    install()
    renderList()
    expect(await screen.findByText('WA-2026-0406')).toBeInTheDocument()
    expect(screen.getByText('Mercedes-Benz GLC · W1KBMRB80MN6DTN3X')).toBeInTheDocument()
    expect(screen.getByText('Reto Wyss')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('serviceOrder.state.accepted'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('serviceOrder.state.cancelled'))).toBeInTheDocument()
    expect(screen.getAllByText('26.11.2026').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Aarau').length).toBe(2)
    // No chips in this PR (Anto, 2026-11-26).
    expect(screen.queryByRole('button', { name: /Rechnungsbereit|Heute/ })).not.toBeInTheDocument()
  })

  it('sends the search and the state filter to the server', async () => {
    const user = userEvent.setup()
    const backend = install()
    const filters = JSON.stringify([{ id: 'state:is', fieldId: 'state', type: 'select', condition: 'is', value: 'cancelled' }])
    renderList(`/aftersales?filters=${encodeURIComponent(filters)}`)
    await screen.findByText('WA-2026-0406')
    expect(backend.callsTo(/^\/service-orders$/, 'GET').at(-1)!.params.get('state')).toBe('cancelled')

    await user.type(screen.getByPlaceholderText(i18n.t('serviceOrders.searchPlaceholder')), 'Wyss')
    await waitFor(() => expect(backend.callsTo(/^\/service-orders$/, 'GET').at(-1)!.params.get('q')).toBe('Wyss'))
    const last = backend.callsTo(/^\/service-orders$/, 'GET').at(-1)!
    expect(last.params.get('state')).toBe('cancelled')
  })

  it('a state the filter does not offer is neither sent nor shown (KAN-458)', async () => {
    const backend = install()
    const filters = JSON.stringify([{ id: 'state:isNot', fieldId: 'state', type: 'select', condition: 'isNot', value: 'cancelled' }])
    renderList(`/aftersales?filters=${encodeURIComponent(filters)}`)
    await screen.findByText('WA-2026-0406')
    expect(backend.callsTo(/^\/service-orders$/, 'GET').every((call) => !call.params.has('state'))).toBe(true)
  })

  it('a sortable header sends its sort to the server; only the columns the API sorts by offer one', async () => {
    const backend = install()
    renderList()
    await screen.findByText('WA-2026-0406')
    expect(backend.callsTo(/^\/service-orders$/, 'GET').at(-1)!.params.get('sort')).toBe('createdAt:desc')

    // Visible by default: number and order date sort; vehicle · party, state and location do not.
    const sortable = screen.getAllByRole('columnheader').filter((h) => h.getAttribute('aria-sort') !== null)
    expect(sortable.map((h) => h.textContent)).toEqual([i18n.t('serviceOrders.columns.orderNumber'), i18n.t('serviceOrders.columns.orderDate')].map((label) => expect.stringContaining(label)))

    await userEvent.click(sortable.find((h) => h.textContent?.includes(i18n.t('serviceOrders.columns.orderDate')))!)
    await waitFor(() => expect(backend.callsTo(/^\/service-orders$/, 'GET').at(-1)!.params.get('sort')).toBe('orderDate:asc'))
  })

  it('a sort from the URL on a field the API does not sort by is not sent', async () => {
    const backend = install()
    renderList('/aftersales?sort=state:asc')
    await screen.findByText('WA-2026-0406')
    expect(backend.callsTo(/^\/service-orders$/, 'GET').every((call) => call.params.get('sort') !== 'state:asc')).toBe(true)
  })

  it('a row opens its order', async () => {
    const user = userEvent.setup()
    install()
    renderList()
    await screen.findByText('WA-2026-0406')
    const [menuButton] = screen.getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await user.click(menuButton)
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('serviceOrder.actions.open')) }))
    expect(await screen.findByText('order page order-1')).toBeInTheDocument()
  })

  it('a cancelled order’s «Auftrag stornieren» is disabled with its reason', async () => {
    const user = userEvent.setup()
    install()
    renderList()
    await screen.findByText('WA-2026-0407')
    const menuButtons = screen.getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await user.click(menuButtons[1])
    const cancel = await screen.findByRole('menuitem', { name: new RegExp(i18n.t('serviceOrder.actions.cancel')) })
    expect(cancel).toHaveAttribute('data-disabled', 'true')
    expect(screen.getByText(i18n.t('serviceOrder.actions.cancelNotInState.cancelled'))).toBeInTheDocument()
  })

  it('«Neuer Auftrag» and «Auftrag stornieren» are hidden from a role that may not write orders (ADR-104)', async () => {
    const user = userEvent.setup()
    install(ALL_GATED_CAPABILITIES.filter((capability) => capability !== 'service_orders'))
    renderList()
    await screen.findByText('WA-2026-0406')
    expect(screen.queryByRole('button', { name: i18n.t('serviceOrders.newOrder') })).not.toBeInTheDocument()
    const [menuButton] = screen.getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await user.click(menuButton)
    await screen.findByRole('menuitem', { name: new RegExp(i18n.t('serviceOrder.actions.open')) })
    expect(screen.queryByRole('menuitem', { name: new RegExp(i18n.t('serviceOrder.actions.cancel')) })).not.toBeInTheDocument()
  })
})

// ui-formats.md: a date-only value (the order date, a backend dt.date) names a calendar day and
// renders the same day west of Greenwich too — the case `new Date('2026-11-26')` gets wrong.
describe('the order date is a calendar day (KAN-810, ui-formats.md)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('shows 26.11.2026 under Pacific/Honolulu', async () => {
    install()
    renderList()
    expect((await screen.findAllByText('26.11.2026')).length).toBeGreaterThan(0)
    expect(screen.queryByText('25.11.2026')).not.toBeInTheDocument()
  })
})
