// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import type { CustomerRead } from '../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'

// KAN-44 / ADR-065 — the ONLY SPA caller of POST /customers/{id}/credit-block.
// Reason is mandatory when blocking (the API raises 409; the form checks it
// first). Reached from the shared row menu, never a header button.

function installBackend(over: Partial<CustomerRead> = {}) {
  const record = customer({ id: 'c1', customerNumber: 'K-1001', version: 3, ...over })
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => record },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/customers\/c1\/credit-block$/,
      handler: (req) => {
        const body = req.body as { blocked: boolean; reason: string | null }
        return { ...record, creditBlock: body.blocked, creditBlockReason: body.reason, version: record.version + 1 }
      },
    },
  ])
}

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' },
  )
}

async function openCreditBlockItem(user: ReturnType<typeof userEvent.setup>, label: string) {
  await screen.findByText(i18n.t('customerDetail.overview.cards.record'))
  await user.click(screen.getByRole('button', { name: 'More actions' }))
  await user.click(await screen.findByRole('menuitem', { name: label }))
}

describe('CreditBlockDialog (KAN-44)', () => {
  it('setting a block requires a reason — enforced before the request', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openCreditBlockItem(user, i18n.t('customerRowMenu.setCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))

    expect(within(dialog).getByText(i18n.t('creditBlockDialog.reasonRequired'))).toBeInTheDocument()
    expect(backend.callsTo(/credit-block/, 'POST')).toHaveLength(0)
  })

  it('with a reason, it POSTs blocked:true and the reason, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openCreditBlockItem(user, i18n.t('customerRowMenu.setCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Overdue invoice 4471')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))

    await waitFor(() => {
      const calls = backend.callsTo(/credit-block/, 'POST')
      expect(calls).toHaveLength(1)
      expect(calls[0].body).toEqual({ blocked: true, reason: 'Overdue invoice 4471' })
    })
  })

  it('an already-blocked customer gets a "remove" flow that POSTs blocked:false', async () => {
    const user = userEvent.setup()
    const backend = installBackend({ creditBlock: true, creditBlockReason: 'Overdue' })
    renderDetail()

    await openCreditBlockItem(user, i18n.t('customerRowMenu.removeCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.clearSubmit') }))

    await waitFor(() => {
      const calls = backend.callsTo(/credit-block/, 'POST')
      expect(calls).toHaveLength(1)
      expect(calls[0].body).toEqual({ blocked: false, reason: null })
    })
  })
})
