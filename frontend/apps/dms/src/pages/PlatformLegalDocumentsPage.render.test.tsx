// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { PlatformLegalDocumentsPage } from './PlatformLegalDocumentsPage'

// KAN-214 (FR-A-39) — Platform Administration › Legal documents: every version
// per language, a draft marked as one; staff publish a new version, never edit.

const draftTos = {
  id: 'doc-1',
  documentType: 'tos',
  version: '0.9',
  language: 'de',
  effectiveDate: '2026-11-14',
  title: 'Nutzungsbedingungen (Entwurf)',
  draft: true,
  bodyMarkdown: 'x',
  acceptanceGraceDays: 30,
  publishedAt: '2026-11-14T00:00:00Z',
}

afterEach(() => {
  cleanup()
})

describe('PlatformLegalDocumentsPage (KAN-214)', () => {
  it('lists the versions with the draft marked, and publishes a new one', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/platform\/legal-documents$/, handler: () => ({ items: [draftTos] }) },
      { method: 'POST', match: /^\/platform\/legal-documents$/, handler: () => status(201, { ...draftTos, id: 'doc-2', version: '1.0', draft: false }) },
    ])
    renderWithProviders(<PlatformLegalDocumentsPage />, { route: '/platform/legal-documents' })

    const table = await screen.findByRole('table', { name: i18n.t('setup.platform.title') })
    expect(within(table).getByText(i18n.t('setup.legal.draft'))).toBeInTheDocument()
    expect(within(table).getByText(draftTos.title)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('setup.platform.publish.button') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.platform.publish.title') })
    const submit = within(dialog).getByRole('button', { name: i18n.t('setup.platform.publish.submit') })
    expect(submit).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox', { name: i18n.t('setup.platform.publish.version') }), '1.0')
    await user.type(within(dialog).getByRole('textbox', { name: i18n.t('setup.platform.publish.documentTitle') }), 'Nutzungsbedingungen')
    await user.type(within(dialog).getByRole('textbox', { name: i18n.t('setup.platform.publish.body') }), 'Text')
    await user.click(submit)

    await waitFor(() => expect(backend.callsTo(/^\/platform\/legal-documents$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/platform\/legal-documents$/, 'POST')[0]!.body).toMatchObject({
      documentType: 'tos',
      version: '1.0',
      language: 'de',
      title: 'Nutzungsbedingungen',
      bodyMarkdown: 'Text',
      acceptanceGraceDays: 30,
    })
  })
})
