// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomerDetailPage } from './CustomerDetailPage'
import { CustomersListPage } from './CustomersListPage'

// KAN-377 (D-A-04): a Betrieb without the sales module gets 404 from every
// `/sales/*` route, so its customer screens neither read offers/contracts nor
// offer "New offer" / "New contract". Presentation only — the 404 stays the
// boundary.
//
// jsdom has no layout, so TanStack Virtual windows down to zero rows and no
// row menu would ever render; the virtualizer is replaced by "render every row".
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

const WORKSHOP_ENTITLEMENTS = ['aftersales', 'parts']

const session = (entitlements: string[]): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: {
      id: 'user-1',
      firstName: 'Test',
      lastName: 'User',
      email: 'test@example.ch',
      accessRoles: [],
    },
    activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
    memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
    entitlements,
  }),
})

function installDetail(entitlements: string[]) {
  return installFakeBackend([
    session(entitlements),
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', customerNumber: 'K-1001', lastName: 'Aebi' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

function renderDetail(route = '/customers/c1') {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route },
  )
}

const offersTab = () => i18n.t('customerDetail.tabs.offersContracts')
const newOffer = () => new RegExp(i18n.t('customerRowMenu.newOffer'))
const newContract = () => new RegExp(i18n.t('customerRowMenu.newContract'))

describe('Customer detail for a Betrieb without sales (KAN-377)', () => {
  it('issues no /sales/* request and shows neither the tab nor a sales action', async () => {
    const user = userEvent.setup()
    const backend = installDetail(WORKSHOP_ENTITLEMENTS)
    renderDetail()

    // The header has rendered, with "Link vehicle" as its alternative.
    expect(await screen.findByRole('button', { name: i18n.t('customerRowMenu.linkVehicle') })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/customers\/c1\/audit-log$/)).not.toHaveLength(0))

    expect(screen.queryByRole('tab', { name: new RegExp(offersTab()) })).toBeNull()
    expect(screen.queryByRole('button', { name: newOffer() })).toBeNull()

    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.mergeInto')) })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: newOffer() })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: newContract() })).toBeNull()

    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })

  it('a pasted ?tab=offersContracts opens the default tab and still calls no sales endpoint', async () => {
    const backend = installDetail(WORKSHOP_ENTITLEMENTS)
    renderDetail('/customers/c1?tab=offersContracts')

    expect(await screen.findByRole('button', { name: i18n.t('customerRowMenu.linkVehicle') })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/customers\/c1\/audit-log$/)).not.toHaveLength(0))
    expect(screen.queryByText(i18n.t('customerDetail.offersContracts.emptyState.title'))).toBeNull()
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('customerDetail.tabs.overview')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })

  it('a Betrieb with sales still reads the deals and offers both actions', async () => {
    const user = userEvent.setup()
    const backend = installDetail(['sales'])
    renderDetail()

    expect(await screen.findByRole('tab', { name: new RegExp(offersTab()) })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: newOffer() })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: newContract() })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/sales\/offers$/, 'GET')).not.toHaveLength(0))
    await waitFor(() => expect(backend.callsTo(/\/sales\/contracts$/, 'GET')).not.toHaveLength(0))
  })
})

describe('Customers list row menu for a Betrieb without sales (KAN-377)', () => {
  it('offers neither "New offer" nor "New contract"', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      session(WORKSHOP_ENTITLEMENTS),
      { method: 'GET', match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1', lastName: 'Aebi' })]) },
    ])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    const [menuButton] = await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await user.click(menuButton)
    expect(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.edit')) })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: newOffer() })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: newContract() })).toBeNull()
    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })
})
