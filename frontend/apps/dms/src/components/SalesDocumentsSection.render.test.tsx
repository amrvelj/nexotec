// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { SalesDocumentsSection } from './SalesDocumentsSection'

// KAN-266 — "Dokument erzeugen" carries an Idempotency-Key, and every
// generate that succeeds renews it: each click is a new version, a retry of
// a lost one is not.

function install() {
  let versions = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/sales\/contracts\/k1\/documents$/,
      handler: () => {
        versions += 1
        return { __status: 201, body: { id: `d${versions}`, version: versions, correspondenceLanguage: 'de' } }
      },
    },
    { method: 'GET', match: /^\/sales\/documents\/d\d+\/pdf$/, handler: () => ({}) },
  ])
}

afterEach(() => cleanup())

describe('SalesDocumentsSection — one Idempotency-Key per generate (KAN-266)', () => {
  it('two generates in a row are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<SalesDocumentsSection ownerType="contract" ownerId="k1" />)

    const generate = await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') })
    await user.click(generate)
    await waitFor(() => expect(backend.callsTo(/\/documents$/, 'POST')).toHaveLength(1))
    await waitFor(() => expect(generate).toBeEnabled())
    await user.click(generate)
    await waitFor(() => expect(backend.callsTo(/\/documents$/, 'POST')).toHaveLength(2))

    const [first, second] = backend.callsTo(/\/documents$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })
})
