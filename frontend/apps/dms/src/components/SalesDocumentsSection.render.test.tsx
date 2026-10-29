// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
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

describe('SalesDocumentsSection — a refused contract document says why (KAN-67)', () => {
  it.each([
    ['missing_vehicle', 'salesDocuments.errors.missingVehicle'],
    ['missing_price', 'salesDocuments.errors.missingPrice'],
  ])('a %s 409 renders the localised reason, and no document', async (reason, key) => {
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/documents$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'Contract C-000001 has no …', details: { reason } } }),
      },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="contract" ownerId="k1" />)

    await user.click(await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') }))

    expect(await screen.findByText(i18n.t(key))).toBeInTheDocument()
    // KAN-322 — the dismissable alert names its close button.
    expect(screen.getByRole('button', { name: i18n.t('common.dismiss') })).toBeInTheDocument()
    expect(screen.getByText(i18n.t('salesDocuments.empty'))).toBeInTheDocument()
  })
})

// KAN-273 — fixed in code by KAN-67 (generate's catch); pinned here.
describe('SalesDocumentsSection — a failed generate says so and keeps its key (KAN-273)', () => {
  it('a dropped generate shows the translated message, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    let attempts = 0
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/offers\/o1\/documents$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) throw new TypeError('Failed to fetch')
          return status(201, { id: 'd1', version: 1, correspondenceLanguage: 'de' })
        },
      },
      { method: 'GET', match: /^\/sales\/documents\/d1\/pdf$/, handler: () => ({}) },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="offer" ownerId="o1" />)

    const generate = await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') })
    await user.click(generate)
    expect(await screen.findByText(i18n.t('salesDocuments.errors.generic'))).toBeInTheDocument()
    await waitFor(() => expect(generate).toBeEnabled())

    await user.click(generate)
    await waitFor(() => expect(backend.callsTo(/\/documents$/, 'POST')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByText(i18n.t('salesDocuments.errors.generic'))).toBeNull())

    const [failedKey, retryKey] = backend.callsTo(/\/documents$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})

// KAN-495 (Anto, 2026-10-29) — an offer with a live contract prints no new
// document: «Dokument erzeugen» is disabled before the click and says why;
// a contract made after the page loaded is refused in the same words.
describe('SalesDocumentsSection — an offer with a live contract generates no document (KAN-495)', () => {
  it('disables «Dokument erzeugen» and names the contract', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="offer" ownerId="o1" liveContractNumber="V-000003" />)

    expect(await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') })).toBeDisabled()
    expect(screen.getByText(i18n.t('salesDocuments.liveContract', { contractNumber: 'V-000003' }))).toBeInTheDocument()
    expect(backend.callsTo(/\/documents$/, 'POST')).toHaveLength(0)
  })

  it('an offer without a live contract keeps the action', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="offer" ownerId="o1" liveContractNumber={null} />)

    expect(await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') })).toBeEnabled()
  })

  it('a 409 offer_has_contract says the same sentence, not the API text', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/offers\/o1\/documents$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'Offer O-000001 has contract V-000004 — edit the contract.',
              details: { reason: 'offer_has_contract', contractNumber: 'V-000004' },
            },
          }),
      },
    ])
    renderWithProviders(<SalesDocumentsSection ownerType="offer" ownerId="o1" />)

    await user.click(await screen.findByRole('button', { name: i18n.t('salesDocuments.generate') }))

    expect(await screen.findByText(i18n.t('salesDocuments.liveContract', { contractNumber: 'V-000004' }))).toBeInTheDocument()
    expect(screen.queryByText(/edit the contract/)).not.toBeInTheDocument()
  })
})
