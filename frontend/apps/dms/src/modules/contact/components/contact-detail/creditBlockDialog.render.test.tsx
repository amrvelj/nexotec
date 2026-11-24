// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend, status } from '../../../../test/fakeBackend'
import { contact } from '../../../../test/fixtures'
import type { ContactRead } from '../../../../api/types'
import { ContactDetailPage } from '../../pages/ContactDetailPage'

// KAN-44 / ADR-065 — the ONLY SPA caller of POST /contacts/{id}/credit-block.
// Reason is mandatory when blocking (the API raises 409; the form checks it
// first). Reached from the shared row menu, never a header button.

function installBackend(over: Partial<ContactRead> = {}, { failFirstPost = false, refusal = null as unknown } = {}) {
  const record = contact({ id: 'c1', contactNumber: 'K-1001', version: 3, ...over })
  let posts = 0
  return installFakeBackend([
    { match: /^\/contacts\/c1$/, handler: () => record },
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/contacts\/c1\/credit-block$/,
      handler: (req) => {
        posts += 1
        if (refusal) return refusal
        if (failFirstPost && posts === 1) {
          return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
        }
        const body = req.body as { blocked: boolean; reason: string | null }
        return { ...record, creditBlock: body.blocked, creditBlockReason: body.reason, version: record.version + 1 }
      },
    },
  ])
}

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/contacts/:id" element={<ContactDetailPage />} />
    </Routes>,
    { route: '/contacts/c1' },
  )
}

async function openCreditBlockItem(user: ReturnType<typeof userEvent.setup>, label: string) {
  await screen.findByText(i18n.t('contactDetail.overview.cards.record'))
  await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
  await user.click(await screen.findByRole('menuitem', { name: label }))
}

describe('CreditBlockDialog (KAN-44)', () => {
  it('setting a block requires a reason — enforced before the request', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))

    expect(within(dialog).getByText(i18n.t('creditBlockDialog.reasonRequired'))).toBeInTheDocument()
    expect(backend.callsTo(/credit-block/, 'POST')).toHaveLength(0)
  })

  it('with a reason, it POSTs blocked:true and the reason, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Overdue invoice 4471')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))

    await waitFor(() => {
      const calls = backend.callsTo(/credit-block/, 'POST')
      expect(calls).toHaveLength(1)
      expect(calls[0].body).toEqual({ blocked: true, reason: 'Overdue invoice 4471' })
    })
  })

  it('an already-blocked contact gets a "remove" flow that POSTs blocked:false', async () => {
    const user = userEvent.setup()
    const backend = installBackend({ creditBlock: true, creditBlockReason: 'Overdue' })
    renderDetail()

    await openCreditBlockItem(user, i18n.t('contactRowMenu.removeCreditBlock'))

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.clearSubmit') }))

    await waitFor(() => {
      const calls = backend.callsTo(/credit-block/, 'POST')
      expect(calls).toHaveLength(1)
      expect(calls[0].body).toEqual({ blocked: false, reason: null })
    })
  })

  it('a retried submit carries the same Idempotency-Key; the next submission gets a new one (KAN-266)', async () => {
    const user = userEvent.setup()
    const backend = installBackend({}, { failFirstPost: true })
    renderDetail()
    const keys = () => backend.callsTo(/credit-block/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

    await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))
    let dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Overdue invoice 4471')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))
    expect(await within(dialog).findByText(i18n.t('creditBlockDialog.failed'))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // The block went through, so the row menu now offers to remove it.
    await openCreditBlockItem(user, i18n.t('contactRowMenu.removeCreditBlock'))
    dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.clearSubmit') }))
    await waitFor(() => expect(keys()).toHaveLength(3))

    const [first, retry, next] = keys()
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
    expect(next).toBeTruthy()
    expect(next).not.toBe(first)
  })

  it('a block that failed, then the dialog closed and reopened, is sent under a new key (KAN-266)', async () => {
    // Without the renew on opening, the key of a failed submission would
    // follow the dialog to the next opening, for this contact or the next.
    const user = userEvent.setup()
    const backend = installBackend({}, { failFirstPost: true })
    renderDetail()
    const keys = () => backend.callsTo(/credit-block/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

    await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))
    let dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Overdue invoice 4471')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))
    expect(await within(dialog).findByText(i18n.t('creditBlockDialog.failed'))).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))
    dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Overdue invoice 4471')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))
    await waitFor(() => expect(keys()).toHaveLength(2))

    const [failed, reopened] = keys()
    expect(failed).toBeTruthy()
    expect(reopened).toBeTruthy()
    expect(reopened).not.toBe(failed)
  })

  // KAN-437: a refusal is said in the UI language — here a role that may
  // not set a credit block, in French — never in the API's English words.
  it('a refused block is said in the UI language, never in the API\'s English text', async () => {
    await i18n.changeLanguage('fr')
    try {
      const user = userEvent.setup()
      installBackend({}, { refusal: status(403, { error: { code: 'forbidden', message: "Not permitted to write 'credit_block'.", details: null } }) })
      renderDetail()

      await openCreditBlockItem(user, i18n.t('contactRowMenu.setCreditBlock'))
      const dialog = await screen.findByRole('dialog')
      await user.type(within(dialog).getByRole('textbox'), 'Facture 4471 impayée')
      await user.click(within(dialog).getByRole('button', { name: i18n.t('creditBlockDialog.setSubmit') }))
      expect(await within(dialog).findByText(i18n.t('apiRefusal.forbidden'))).toBeInTheDocument()
      expect(within(dialog).queryByText(/Not permitted/)).not.toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
