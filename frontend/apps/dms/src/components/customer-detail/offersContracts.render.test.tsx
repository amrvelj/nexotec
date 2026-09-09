// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { toCustomerDealRows } from './OffersContractsTab'
import type { SalesContractRead, SalesOfferRead } from '../../api/types'

// KAN-45 / FR-06 tab 3, ADR-050 — the "Transactions" tab read the retired
// `transaction` table; it is now "Offers & contracts", the Sales grid
// embedded and filtered by this customer.
//
// NOTE: ui-kit's DataGrid virtualises its rows, and jsdom gives the scroll
// container 0 height, so cell text never renders in these tests. We assert
// the grid's own `aria-rowcount` and the tab-count badge instead — the
// same approach the CustomersListPage render tests take (headers only).

const offer = (over: Partial<SalesOfferRead> = {}): SalesOfferRead =>
  ({
    id: 'o1',
    offerNumber: 'O-000001',
    status: 'open',
    customerId: 'c1',
    customerLabel: 'Anna Muster',
    vehicleLabel: 'VW Golf',
    grossPrice: '32000.00',
    margin: null,
    updatedAt: '2026-02-01T10:00:00Z',
    ...over,
  }) as SalesOfferRead

const contract = (over: Partial<SalesContractRead> = {}): SalesContractRead =>
  ({
    id: 'k1',
    contractNumber: 'C-000001',
    offerId: null,
    status: 'confirmed',
    customerId: 'c1',
    customerLabel: 'Anna Muster',
    vehicleLabel: 'Audi A3',
    grossPrice: '41000.00',
    margin: null,
    updatedAt: '2026-03-01T10:00:00Z',
    ...over,
  }) as SalesContractRead

// --- the row model (pure) --------------------------------------------------

describe('toCustomerDealRows (KAN-45)', () => {
  it('merges offers and contracts, newest first', () => {
    const rows = toCustomerDealRows([offer()], [contract()])
    expect(rows.map((r) => r.number)).toEqual(['C-000001', 'O-000001'])
    expect(rows.map((r) => r.entityType)).toEqual(['contract', 'offer'])
    expect(rows[1].href).toBe('/sales/offers/o1')
    expect(rows[0].href).toBe('/sales/contracts/k1')
  })

  it('folds an offer that became a contract into the one contract row', () => {
    const rows = toCustomerDealRows([offer({ id: 'o1' })], [contract({ id: 'k1', offerId: 'o1' })])
    expect(rows.map((r) => r.number)).toEqual(['C-000001'])
  })

  it('is empty for a customer with neither', () => {
    expect(toCustomerDealRows([], [])).toEqual([])
  })
})

// --- the embedded grid ---------------------------------------------------

function install(offers: SalesOfferRead[], contracts: SalesContractRead[]): FakeBackend {
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'GET',
      match: /^\/sales\/offers$/,
      handler: () => ({ items: offers, nextCursor: null, total: offers.length, totalIsEstimate: false }),
    },
    {
      method: 'GET',
      match: /^\/sales\/contracts$/,
      handler: () => ({ items: contracts, nextCursor: null, total: contracts.length, totalIsEstimate: false }),
    },
    {
      method: 'POST',
      match: /^\/sales\/offers$/,
      handler: () => ({ id: 'new-offer', offerNumber: 'O-000009', version: 1 }),
    },
    { method: 'PATCH', match: /^\/sales\/offers\/new-offer$/, handler: (req) => ({ id: 'new-offer', ...(req.body as object) }) },
  ])
}

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1?tab=offersContracts' },
  )
}

const tabButton = () =>
  screen.getByRole('tab', { name: new RegExp(i18n.t('customerDetail.tabs.offersContracts')) })

describe('customer 360 — offers & contracts tab (KAN-45)', () => {
  it('renders the Sales grid columns and both kinds of row, tab count = their sum', async () => {
    install([offer()], [contract()])
    renderDetail()

    // "the same grid the Sales overview uses" — its column set.
    expect(await screen.findByRole('columnheader', { name: i18n.t('salesList.columns.number') })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: i18n.t('salesList.columns.type') })).toBeInTheDocument()

    await waitFor(() =>
      expect(screen.getByRole('table').getAttribute('aria-rowcount')).toBe('2'),
    )
    await waitFor(() => expect(within(tabButton()).getByText('2')).toBeInTheDocument())
  })

  it('an offer that became a contract counts once', async () => {
    install([offer({ id: 'o1' })], [contract({ id: 'k1', offerId: 'o1' })])
    renderDetail()

    await waitFor(() => expect(screen.getByRole('table').getAttribute('aria-rowcount')).toBe('1'))
    await waitFor(() => expect(within(tabButton()).getByText('1')).toBeInTheDocument())
  })

  it('a customer with neither shows the empty state, and New offer works', async () => {
    const backend = install([], [])
    renderDetail()
    const user = userEvent.setup()

    const emptyState = (
      await screen.findByText(i18n.t('customerDetail.offersContracts.emptyState.title'))
    ).parentElement as HTMLElement

    await user.click(within(emptyState).getByRole('button', { name: i18n.t('salesList.newOffer') }))
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1))
  })

  it('does not call the retired /transactions endpoint', async () => {
    const backend = install([offer()], [])
    renderDetail()
    await screen.findByRole('columnheader', { name: i18n.t('salesList.columns.number') })
    expect(backend.callsTo(/\/transactions/)).toHaveLength(0)
  })
})
