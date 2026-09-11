// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import type { CustomerRead } from '../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'

// KAN-44 / FR-18 region 1: the blocking facts are a STRIP ABOVE THE CARDS,
// absent entirely when neither applies; and FR-22 / FR-21 / ADR-065 — a
// blocked customer may be quoted but not contracted.

function installBackend(over: Partial<CustomerRead> = {}) {
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', customerNumber: 'K-1001', ...over }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
    // KAN-58 — the enabled "New contract" test below actually clicks it.
    {
      method: 'POST',
      match: /^\/sales\/contracts$/,
      handler: () => ({ id: 'new-contract', contractNumber: 'C-000009', version: 1 }),
    },
  ])
}

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' },
  )
}

const blockTitle = i18n.t('customerDetail.overview.blockingStrip.creditBlockTitle')
const dncNote = i18n.t('customerDetail.overview.blockingStrip.doNotContactNote')

describe('BlockingFactsStrip (FR-18 region 1)', () => {
  it('a credit-blocked customer gets a red strip carrying the reason verbatim', async () => {
    installBackend({ creditBlock: true, creditBlockReason: 'Overdue invoice 4471' })
    renderDetail()

    const strip = await screen.findByRole('alert', { name: blockTitle })
    expect(strip).toHaveTextContent('Overdue invoice 4471')
  })

  it('a customer with neither flag has NO strip element in the tree', async () => {
    installBackend()
    renderDetail()

    // Wait for the overview to actually render (a card title shows up).
    await screen.findByText(i18n.t('customerDetail.overview.cards.record'))
    expect(screen.queryAllByRole('alert')).toHaveLength(0)
  })

  it('both flags render two strips, blocked first', async () => {
    installBackend({ creditBlock: true, creditBlockReason: 'x', lifecycleStatus: 'do_not_contact' })
    renderDetail()

    await screen.findByRole('alert', { name: blockTitle })
    const alerts = screen.getAllByRole('alert')
    expect(alerts).toHaveLength(2)
    expect(alerts[0]).toHaveAccessibleName(blockTitle)
    expect(alerts[1]).toHaveTextContent(dncNote)
  })

  it('do-not-contact only: amber strip shows, and the header "New offer" action is disabled', async () => {
    installBackend({ lifecycleStatus: 'do_not_contact' })
    renderDetail()

    expect(await screen.findByText(dncNote)).toBeInTheDocument()
    const newOffer = screen.getByRole('button', { name: i18n.t('customerRowMenu.newOffer') })
    expect(newOffer).toBeDisabled()
  })

  it('blocked only: "New offer" is ENABLED; "New contract" is disabled and its reason names the block reason', async () => {
    const user = userEvent.setup()
    installBackend({ creditBlock: true, creditBlockReason: 'Overdue invoice 4471' })
    renderDetail()

    await screen.findByRole('alert', { name: blockTitle })

    const newOffer = screen.getByRole('button', { name: i18n.t('customerRowMenu.newOffer') })
    expect(newOffer).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'More actions' }))
    const newContract = await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.newContract')) })
    expect(newContract).toHaveAttribute('data-disabled', 'true')
    expect(newContract).toHaveTextContent('Overdue invoice 4471')
  })

  // KAN-58 — the behaviour change this ticket actually makes: with neither
  // flag, "New contract" moves from permanently-disabled ("not available
  // yet") to a real, enabled action that posts a customer-attached contract.
  it('neither flag: "New contract" is ENABLED in the overflow, and clicking it creates a contract for this customer', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await screen.findByText(i18n.t('customerDetail.overview.cards.record'))

    await user.click(screen.getByRole('button', { name: 'More actions' }))
    const newContract = await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.newContract')) })
    expect(newContract).not.toHaveAttribute('data-disabled', 'true')

    await user.click(newContract)
    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/sales\/contracts$/, 'POST')[0].body).toEqual({ customerId: 'c1' })
  })
})
