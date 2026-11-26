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

// KAN-810 (Anto, 2026-11-26): «Gesperrt» — the ONLY SPA caller of
// POST /contacts/{id}/block. Its own row-menu entry and dialog beside the
// credit block's; the reason is required to place it; the strip above the
// cards names it with its reason.

function installBackend(over: Partial<ContactRead> = {}, { refusal = null as unknown } = {}) {
  let record = contact({ id: 'c1', contactNumber: 'K-1001', version: 3, ...over })
  return installFakeBackend([
    { match: /^\/contacts\/c1$/, handler: () => record },
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/contacts\/c1\/block$/,
      handler: (req) => {
        if (refusal) return refusal
        const body = req.body as { blocked: boolean; reason: string | null }
        record = {
          ...record,
          blocked: body.blocked,
          blockedReason: body.reason,
          blockedAt: body.blocked ? '2026-11-26T09:00:00Z' : null,
          version: record.version + 1,
        }
        return record
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

async function openMenuItem(user: ReturnType<typeof userEvent.setup>, label: string) {
  await screen.findByText(i18n.t('contactDetail.overview.cards.record'))
  await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
  await user.click(await screen.findByRole('menuitem', { name: label }))
}

describe('ContactBlockDialog — «Gesperrt» (KAN-810)', () => {
  it('placing the block requires a reason — said before any request', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openMenuItem(user, i18n.t('contactRowMenu.setBlock'))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('contactBlockDialog.setTitle') })
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contactBlockDialog.setSubmit') }))

    expect(within(dialog).getByText(i18n.t('contactBlockDialog.reasonRequired'))).toBeInTheDocument()
    expect(backend.callsTo(/\/block$/, 'POST')).toHaveLength(0)
  })

  it('with a reason it POSTs to /block (not the credit block), and the strip names it', async () => {
    const user = userEvent.setup()
    const backend = installBackend()
    renderDetail()

    await openMenuItem(user, i18n.t('contactRowMenu.setBlock'))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Hausverbot')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contactBlockDialog.setSubmit') }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const calls = backend.callsTo(/\/block$/, 'POST')
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toEqual({ blocked: true, reason: 'Hausverbot' })
    expect(calls[0].headers.get('If-Match')).toBe('3')
    expect(backend.callsTo(/credit-block/, 'POST')).toHaveLength(0)

    expect(await screen.findByText(i18n.t('contactDetail.overview.blockingStrip.blockedTitle'))).toBeInTheDocument()
    expect(screen.getByText('Hausverbot')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('contactDetail.overview.blockingStrip.creditBlockTitle'))).not.toBeInTheDocument()
  })

  it('a blocked contact is offered «lift», which POSTs blocked:false', async () => {
    const user = userEvent.setup()
    const backend = installBackend({ blocked: true, blockedReason: 'Hausverbot', blockedAt: '2026-11-01T08:00:00Z' })
    renderDetail()

    expect(await screen.findByText(i18n.t('contactDetail.overview.blockingStrip.blockedTitle'))).toBeInTheDocument()
    await openMenuItem(user, i18n.t('contactRowMenu.removeBlock'))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('contactBlockDialog.clearTitle') })
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contactBlockDialog.clearSubmit') }))

    await waitFor(() => expect(backend.callsTo(/\/block$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/block$/, 'POST')[0].body).toEqual({ blocked: false, reason: null })
    await waitFor(() =>
      expect(screen.queryByText(i18n.t('contactDetail.overview.blockingStrip.blockedTitle'))).not.toBeInTheDocument(),
    )
  })

  it('a refusal that names its reason is said in the UI language, not as a version conflict', async () => {
    await i18n.changeLanguage('it')
    try {
      const user = userEvent.setup()
      installBackend(
        {},
        { refusal: status(409, { error: { code: 'conflict', message: 'A reason is required.', details: { reason: 'contact_block_reason_required' } } }) },
      )
      renderDetail()

      await openMenuItem(user, i18n.t('contactRowMenu.setBlock'))
      const dialog = await screen.findByRole('dialog')
      await user.type(within(dialog).getByRole('textbox'), '   x')
      await user.click(within(dialog).getByRole('button', { name: i18n.t('contactBlockDialog.setSubmit') }))
      expect(
        await within(dialog).findByText(i18n.t('apiRefusal.reasons.contact_block_reason_required')),
      ).toBeInTheDocument()
      expect(within(dialog).queryByText(i18n.t('contactBlockDialog.conflict'))).not.toBeInTheDocument()
      expect(within(dialog).queryByText(/A reason is required/)).not.toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })

  it('who may not write the customer role is not offered the block', async () => {
    // Same terms as the credit block (Anto, 2026-11-26): a supplier-only
    // contact has no customer role, so neither block is offered.
    const user = userEvent.setup()
    installBackend({ roles: ['supplier'] })
    renderDetail()

    await screen.findByText(i18n.t('contactDetail.overview.cards.record'))
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    await screen.findByRole('menuitem', { name: i18n.t('contactRowMenu.copyContactNumber') })
    expect(screen.queryByRole('menuitem', { name: i18n.t('contactRowMenu.setBlock') })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: i18n.t('contactRowMenu.setCreditBlock') })).not.toBeInTheDocument()
  })
})
