// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesOfferRead } from '../api/types'
import { OfferCreateRedirectPage, OfferDetailContent } from './OfferDetailPage'

// KAN-266 — `/sales/offers/new` creates its offer under an Idempotency-Key,
// once per visit; the offer's copy carries a key and a successful copy
// retires it.

const OFFER = { id: 'o1', offerNumber: 'O-000001', status: 'open', version: 2, vehicleLabel: 'Seat Leon' } as unknown as SalesOfferRead

function install() {
  let copies = 0
  return installFakeBackend([
    { method: 'POST', match: /^\/sales\/offers$/, handler: () => ({ __status: 201, body: { ...OFFER, id: 'o9', status: 'draft' } }) },
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
    { match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/sales\/offers\/o1\/copy$/,
      handler: () => {
        copies += 1
        return { __status: 201, body: { ...OFFER, id: `copy-${copies}`, status: 'draft', version: 1 } }
      },
    },
  ])
}

afterEach(() => cleanup())

describe('OfferCreateRedirectPage — one create per visit, under a key (KAN-266)', () => {
  it('sends one keyed POST and opens the new offer, under StrictMode too', async () => {
    const backend = install()
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/new" element={<OfferCreateRedirectPage />} />
        <Route path="/sales/offers/:id" element={<p>offer opened</p>} />
      </Routes>,
      { route: '/sales/offers/new', strictMode: true },
    )

    expect(await screen.findByText('offer opened')).toBeInTheDocument()
    const creates = backend.callsTo(/^\/sales\/offers$/, 'POST')
    expect(creates).toHaveLength(1)
    expect(creates[0].headers.get('Idempotency-Key')).toBeTruthy()
  })
})

describe('OfferDetailContent — the copy carries a key (KAN-266)', () => {
  it('two copies of the same offer are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<OfferDetailContent offerId="o1" embedded />)

    const copy = await screen.findByRole('button', { name: i18n.t('offerDetail.actions.copy') })
    await user.click(copy)
    await waitFor(() => expect(backend.callsTo(/\/copy$/, 'POST')).toHaveLength(1))
    await waitFor(() => expect(copy).toBeEnabled())
    await user.click(copy)
    await waitFor(() => expect(backend.callsTo(/\/copy$/, 'POST')).toHaveLength(2))

    const [first, second] = backend.callsTo(/\/copy$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })
})

// KAN-273 — a failed create or copy says so; its retry carries the same key.
describe('OfferDetailPage — a failed create or copy says so and keeps its key (KAN-273)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('/sales/offers/new shows a failed create instead of an endless loader, and Retry reuses the key', async () => {
    const user = userEvent.setup()
    let attempts = 0
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /^\/sales\/offers$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Number range unavailable', details: null } })
          return status(201, { ...OFFER, id: 'o9', status: 'draft' })
        },
      },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/new" element={<OfferCreateRedirectPage />} />
        <Route path="/sales/offers/:id" element={<p>offer opened</p>} />
      </Routes>,
      { route: '/sales/offers/new', strictMode: true },
    )

    expect((await screen.findByRole('alert')).textContent).toContain('Number range unavailable')
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: i18n.t('common.retry') }))
    expect(await screen.findByText('offer opened')).toBeInTheDocument()

    const [failedKey, retryKey] = backend.callsTo(/^\/sales\/offers$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a dropped create names what failed and that the request did not get through', async () => {
    await i18n.changeLanguage('it')
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /^\/sales\/offers$/,
        handler: () => {
          throw new TypeError('Failed to fetch')
        },
      },
    ])
    renderWithProviders(<OfferCreateRedirectPage />, { route: '/sales/offers/new' })

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(i18n.t('offerDetail.errors.createFailed'))
    expect(alert.textContent).toContain(i18n.t('common.requestFailed'))

    // Switching the UI language rewords the failure; only Retry sends again.
    await act(async () => {
      await i18n.changeLanguage('fr')
    })
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain(i18n.t('common.requestFailed')))
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
  })

  it('a dropped copy shows a translated message, and the retry reuses the key', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    let attempts = 0
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
      { match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/offers\/o1\/copy$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) throw new TypeError('Failed to fetch')
          return status(201, { ...OFFER, id: 'copy-1', status: 'draft', version: 1 })
        },
      },
    ])
    renderWithProviders(<OfferDetailContent offerId="o1" embedded />)

    const copy = await screen.findByRole('button', { name: i18n.t('offerDetail.actions.copy') })
    await user.click(copy)
    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('offerDetail.errors.copyFailed'))
    await waitFor(() => expect(copy).toBeEnabled())

    await user.click(copy)
    await waitFor(() => expect(backend.callsTo(/\/copy$/, 'POST')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    const [failedKey, retryKey] = backend.callsTo(/\/copy$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})
