// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { SalesDocumentsSection } from './SalesDocumentsSection'
import { ValuationCreateDialog } from './ValuationCreateDialog'

// KAN-422 — the kit's Picker and DocumentPreview no longer carry English
// defaults («No matches», «Loading…»), and these screens showed them: the
// valuation's customer picker passed no empty label, and the document
// preview's loading line was hardcoded. In French, each says it in French.

const ENGLISH = /^(No matches|Loading…|Download|Not set)$/

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('kit labels on the app screens, in French (KAN-422)', () => {
  it('the valuation’s customer picker says «no matches» in the UI language', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
      { method: 'GET', match: /^\/customers$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('valuationCreate.searchCustomer') }))
    const picker = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.searchCustomer') })
    await user.type(within(picker).getByRole('textbox'), 'zzz')

    expect(await within(picker).findByText(i18n.t('common.noMatches'), {}, { timeout: 2000 })).toBeInTheDocument()
    expect(i18n.t('common.noMatches')).toBe('Aucun résultat')
    expect(within(picker).queryByText(ENGLISH)).toBeNull()
  })

  it('the document preview’s loading line and download button are in the UI language', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/sales\/contracts\/k1\/documents$/,
        handler: () => ({
          items: [{ id: 'd1', version: 1, correspondenceLanguage: 'fr', renderedAt: '2026-10-28T08:00:00Z' }],
          nextCursor: null,
        }),
      },
      // The PDF stays in flight, so the preview shows its loading line.
      { method: 'GET', match: /^\/sales\/documents\/d1\/pdf$/, handler: () => new Promise(() => {}) },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="contract" ownerId="k1" />)

    await user.click(await screen.findByText(i18n.t('salesDocuments.version', { version: 1 })))

    expect(await screen.findByText(i18n.t('common.loading'))).toBeInTheDocument()
    expect(i18n.t('common.loading')).not.toBe('Loading…')
    expect(screen.getByRole('button', { name: i18n.t('salesDocuments.download') })).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).toBeNull()
  })
})
