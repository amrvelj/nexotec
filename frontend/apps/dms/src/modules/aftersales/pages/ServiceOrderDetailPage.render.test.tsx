// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../../../test/fakeBackend'
import type { ServiceOrderRead } from '../../../api/types'
import { ServiceOrderDetailPage } from './ServiceOrderDetailPage'
import { HISTORY, ORDER_ID, order } from '../test/serviceOrderFixtures'

// KAN-810 criteria 2–6 (D3, D16): the order page — the header with one primary and the row menu in
// its overflow, the positions with net and gross per line, the total per payer in two parts with
// the rounding line, the overview's parties changed under If-Match, the history from the audit
// trail, and the cancellation with its reason, who and when.

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options)
const ORDER_PATH = new RegExp(`^/service-orders/${ORDER_ID}$`)

function me(writable: string[]): FakeRoute {
  return {
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
  }
}

function install(options: { current?: ServiceOrderRead; extra?: FakeRoute[]; writable?: string[] } = {}) {
  return installFakeBackend([
    ...(options.extra ?? []),
    me(options.writable ?? ALL_GATED_CAPABILITIES),
    { method: 'GET', match: ORDER_PATH, handler: () => options.current ?? order() },
    { method: 'GET', match: /\/history$/, handler: () => HISTORY },
  ])
}

function renderPage(search = '') {
  renderWithProviders(
    <Routes>
      <Route path="/aftersales/orders/:id" element={<ServiceOrderDetailPage />} />
    </Routes>,
    { route: `/aftersales/orders/${ORDER_ID}${search}` },
  )
}

describe('the workshop order page (KAN-810, D3)', () => {
  it('the header names vehicle and ordering party, the number and the state; the positions show net and gross', async () => {
    install()
    renderPage()

    expect(await screen.findByText('Mercedes-Benz GLC · W1KBMRB80MN6DTN3X · Reto Wyss')).toBeInTheDocument()
    expect(screen.getByText('WA-2026-0406')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('serviceOrder.actions.addJob') })).toBeEnabled()

    const job = screen.getByTestId('job-job-1')
    expect(within(job).getByText('Jahresservice Flotte')).toBeInTheDocument()
    expect(within(job).getByText(t('serviceOrder.billingCategory.customer'))).toBeInTheDocument()
    expect(within(job).getByText(t('serviceOrder.answerState.accepted'))).toBeInTheDocument()

    const labour = screen.getByTestId('position-pos-1')
    // Check 47: 9 AW at CHF 100.00 an hour, 12 AW an hour, is CHF 75.00 net; the position shows «9 AW».
    expect(within(labour).getByText('9 AW')).toBeInTheDocument()
    expect(within(labour).getByText("CHF 100.00")).toBeInTheDocument()
    expect(within(labour).getByText('CHF 75.00')).toBeInTheDocument()
    expect(within(labour).getByText('CHF 81.08')).toBeInTheDocument()
    expect(within(screen.getByTestId('position-pos-2')).getByText('Ersatzwagen bis 17 Uhr')).toBeInTheDocument()
  })

  it('the total per payer comes in two parts, accepted and still open, with VAT and «Rundung»', async () => {
    install()
    renderPage()

    const accepted = await screen.findByTestId('totals-accepted')
    // Check 13: net 434.85 plus VAT 35.22, rounding −0.02, total 470.05.
    expect(within(accepted).getByText('CHF 434.85')).toBeInTheDocument()
    expect(within(accepted).getByText(t('serviceOrder.totals.vatLine', { rate: '8.1' }))).toBeInTheDocument()
    expect(within(accepted).getByText('CHF 35.22')).toBeInTheDocument()
    expect(within(accepted).getByText(t('serviceOrder.totals.rounding'))).toBeInTheDocument()
    expect(within(accepted).getByText('− CHF 0.02')).toBeInTheDocument()
    expect(within(accepted).getByText('CHF 470.05')).toBeInTheDocument()
    expect(within(accepted).getByText('Reto Wyss')).toBeInTheDocument()
    const open = screen.getByTestId('totals-open')
    expect(within(open).getByText('CHF 120.00')).toBeInTheDocument()
    expect(within(open).getByText('CHF 129.70')).toBeInTheDocument()
  })

  it('«Gruppe hinzufügen» posts the job with the order’s version and the billing category «Kunde»', async () => {
    const user = userEvent.setup()
    const backend = install({ extra: [{ method: 'POST', match: /\/jobs$/, handler: () => order({ version: 4 }) }] })
    renderPage()

    await user.click(await screen.findByRole('button', { name: t('serviceOrder.actions.addJob') }))
    await user.type(screen.getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.jobs.dialog.name')) }), 'Bremsen')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.jobs.dialog.add') }))
    await waitFor(() => expect(backend.callsTo(/\/jobs$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/jobs$/, 'POST')
    expect(post.body).toEqual({ name: 'Bremsen', billingCategory: 'customer' })
    expect(post.headers.get('If-Match')).toBe('3')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a stale version is said, and «Neu laden» reads the order again', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [
        {
          method: 'POST',
          match: /\/jobs$/,
          handler: () => status(409, { error: { code: 'conflict', message: 'stale', details: { currentVersion: 5 } } }),
        },
      ],
    })
    renderPage()

    await user.click(await screen.findByRole('button', { name: t('serviceOrder.actions.addJob') }))
    await user.type(screen.getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.jobs.dialog.name')) }), 'Bremsen')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.jobs.dialog.add') }))
    expect(await screen.findByRole('alert')).toHaveTextContent(t('apiRefusal.staleVersion'))
    const reads = backend.callsTo(ORDER_PATH, 'GET').length
    await user.click(screen.getByRole('button', { name: t('serviceOrder.reload') }))
    await waitFor(() => expect(backend.callsTo(ORDER_PATH, 'GET').length).toBeGreaterThan(reads))
  })

  it('a job is renamed under If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [{ method: 'PATCH', match: new RegExp(`^/service-orders/${ORDER_ID}/jobs/job-1$`), handler: () => order({ version: 4 }) }],
    })
    renderPage()

    await user.click((await screen.findAllByRole('button', { name: t('serviceOrder.jobs.rename') }))[0])
    const name = await screen.findByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.jobs.dialog.name')) })
    await user.clear(name)
    await user.type(name, 'Jahresservice Flotte 2027')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(backend.callsTo(/\/jobs\/job-1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/jobs\/job-1$/, 'PATCH')
    expect(patch.body).toEqual({ name: 'Jahresservice Flotte 2027' })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('a position is deleted after a confirmation, under If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [{ method: 'DELETE', match: /\/positions\/pos-1$/, handler: () => order({ version: 4 }) }],
    })
    renderPage()

    await user.click((await screen.findAllByRole('button', { name: t('serviceOrder.positions.delete') }))[0])
    expect(backend.callsTo(/\/positions\/pos-1$/, 'DELETE')).toHaveLength(0)
    await user.click(await screen.findByRole('button', { name: t('serviceOrder.positions.confirmDelete') }))

    await waitFor(() => expect(backend.callsTo(/\/positions\/pos-1$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/\/positions\/pos-1$/, 'DELETE')[0].headers.get('If-Match')).toBe('3')
  })

  it('the driver is removed under If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({
      current: order({ driver: { contactId: 'contact-9', label: 'Luca Fahrer' } }),
      extra: [{ method: 'PATCH', match: ORDER_PATH, handler: () => order({ version: 4 }) }],
    })
    renderPage('?tab=overview')

    await user.click(await screen.findByRole('button', { name: t('serviceOrder.overview.removeDriver') }))

    await waitFor(() => expect(backend.callsTo(ORDER_PATH, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(ORDER_PATH, 'PATCH')
    expect(patch.body).toEqual({ driverContactId: null })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('the overview renders the order date as a calendar day under Pacific/Honolulu (ui-formats.md)', async () => {
    const tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
    try {
      install()
      renderPage('?tab=overview')
      expect((await screen.findAllByText('26.11.2026')).length).toBeGreaterThan(0)
      expect(screen.queryByText('25.11.2026')).not.toBeInTheDocument()
    } finally {
      if (tz === undefined) delete process.env.TZ
      else process.env.TZ = tz
    }
  })

  it('a frozen order shows the party changes disabled (ADR-061/104: a state disables, only a role hides)', async () => {
    install({ current: order({ version: 4, state: 'cancelled' }) })
    renderPage('?tab=overview')

    const change = await screen.findByRole('button', {
      name: t('serviceOrder.overview.changeTitle', { role: t('serviceOrder.overview.invoiceRecipient') }),
    })
    expect(change).toBeDisabled()
  })

  it('without the role the party changes are hidden', async () => {
    install({ writable: ALL_GATED_CAPABILITIES.filter((c) => c !== 'service_orders') })
    renderPage('?tab=overview')

    await screen.findByText(t('serviceOrder.overview.invoiceRecipient'))
    expect(
      screen.queryByRole('button', { name: t('serviceOrder.overview.changeTitle', { role: t('serviceOrder.overview.invoiceRecipient') }) }),
    ).not.toBeInTheDocument()
  })

  it('the overview changes the invoice recipient by contact search, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [
        {
          method: 'GET',
          match: /^\/contacts$/,
          handler: () => ({
            items: [{ id: 'contact-7', contactNumber: 'K-000007', contactType: 'business', companyName: 'Pharmacie du Lac SA', firstName: null, lastName: null }],
            nextCursor: null,
            total: 1,
            totalIsEstimate: false,
          }),
        },
        { method: 'PATCH', match: ORDER_PATH, handler: () => order({ version: 4, invoiceRecipient: { contactId: 'contact-7', label: 'Pharmacie du Lac SA' } }) },
      ],
    })
    renderPage('?tab=overview')

    expect(await screen.findByTestId('party-invoiceRecipient')).toHaveTextContent('Reto Wyss')
    expect(screen.getByText('26.11.2026')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('serviceOrder.overview.changeTitle', { role: t('serviceOrder.overview.invoiceRecipient') }) }))
    await user.type(await screen.findByRole('textbox', { name: t('serviceOrder.overview.contactSearchLabel') }), 'Pharmacie')
    await user.click(await screen.findByRole('option', { name: /Pharmacie du Lac SA/ }))

    await waitFor(() => expect(backend.callsTo(ORDER_PATH, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(ORDER_PATH, 'PATCH')
    expect(patch.body).toEqual({ invoiceRecipientContactId: 'contact-7' })
    expect(patch.headers.get('If-Match')).toBe('3')
    expect(await screen.findByTestId('party-invoiceRecipient')).toHaveTextContent('Pharmacie du Lac SA')
  })

  it('the history is the audit trail, newest first, with who, when and before → after', async () => {
    install()
    renderPage('?tab=history')

    const entries = await screen.findAllByTestId('history-entry')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent(t('serviceOrder.history.actions.service_order_parties_changed'))
    expect(entries[0]).toHaveTextContent('Marc Perrin')
    expect(entries[0]).toHaveTextContent(`${t('serviceOrder.history.fields.invoiceRecipientLabel')}: Reto Wyss → Pharmacie du Lac SA`)
    expect(entries[1]).toHaveTextContent(t('serviceOrder.history.actions.service_order_created'))
    expect(entries[1]).toHaveTextContent('Sandra Keller')
  })
})

describe('cancelling the order (KAN-810 criterion 5, D16)', () => {
  const CANCELLED = order({
    version: 4,
    state: 'cancelled',
    cancellation: { cancelledAt: '2026-11-26T10:15:00Z', cancelledBy: 'user-1', cancelledByLabel: 'Sandra Keller', reason: 'Kunde verkauft das Auto' },
  })

  async function openCancel(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: t('common.moreActions') }))
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(t('serviceOrder.actions.cancel')) }))
  }

  it('needs a reason, sends it with If-Match, and the page then shows who, when and why', async () => {
    const user = userEvent.setup()
    const backend = install({ extra: [{ method: 'POST', match: /\/cancel$/, handler: () => CANCELLED }] })
    renderPage()

    await openCancel(user)
    const submit = await screen.findByRole('button', { name: t('serviceOrder.cancel.submit') })
    // Never a held-back button: a submit without a reason says so and sends nothing.
    await user.click(submit)
    expect(await screen.findByText(t('serviceOrder.cancel.reasonMissing'))).toBeInTheDocument()
    expect(backend.callsTo(/\/cancel$/, 'POST')).toHaveLength(0)
    await user.type(screen.getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.cancel.reason')) }), 'Kunde verkauft das Auto')
    await user.click(submit)

    await waitFor(() => expect(backend.callsTo(/\/cancel$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/cancel$/, 'POST')
    expect(post.body).toEqual({ reason: 'Kunde verkauft das Auto' })
    expect(post.headers.get('If-Match')).toBe('3')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()

    const banner = await screen.findByTestId('cancellation')
    expect(banner).toHaveTextContent('Sandra Keller')
    expect(banner).toHaveTextContent('Kunde verkauft das Auto')
    expect(banner).toHaveTextContent('26.11.2026')
    // Frozen: «Gruppe hinzufügen» is disabled (the reason is its tooltip), the menu entry too.
    expect(screen.getByRole('button', { name: t('serviceOrder.actions.addJob') })).toBeDisabled()
  })

  it('a refusal (a clock has run) is said in one sentence', async () => {
    const user = userEvent.setup()
    install({
      extra: [
        {
          method: 'POST',
          match: /\/cancel$/,
          handler: () => status(409, { error: { code: 'conflict', message: 'Time has been clocked', details: { reason: 'service_order_clock_has_run' } } }),
        },
      ],
    })
    renderPage()

    await openCancel(user)
    await user.type(screen.getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.cancel.reason')) }), 'Doppelt erfasst')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.cancel.submit') }))
    expect(await screen.findByRole('alert')).toHaveTextContent(t('apiRefusal.reasons.service_order_clock_has_run'))
  })

  it('a role that may not write orders sees neither the primary nor the cancel entry (ADR-104)', async () => {
    install({ writable: ALL_GATED_CAPABILITIES.filter((capability) => capability !== 'service_orders') })
    renderPage()
    await screen.findByText('WA-2026-0406')
    expect(screen.queryByRole('button', { name: t('serviceOrder.actions.addJob') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('common.moreActions') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('serviceOrder.jobs.addPosition') })).not.toBeInTheDocument()
  })
})
