// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesContractRead } from '../api/types'
import { ContractDetailContent, ContractDetailPage } from './ContractDetailPage'

// KAN-266 — the contract screen's confirm and cancel carry an
// Idempotency-Key beside their If-Match. A confirm retried after a failure
// keeps its key, so the server replays a confirmation whose response was
// lost instead of answering the stale If-Match with a version conflict.

const CONTRACT = {
  id: 'k1',
  contractNumber: 'C-000001',
  offerNumber: 'O-000001',
  status: 'pending',
  version: 3,
  vehicleSource: 'stock',
  vehicleLabel: 'Seat Leon',
  grossPrice: '32000.00',
  tradeInValue: null,
  payable: null,
  margin: null,
  financing: null,
} as unknown as SalesContractRead

function install({ failFirstConfirm = false } = {}) {
  let confirms = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
    { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/sales\/contracts\/k1\/confirm$/,
      handler: () => {
        confirms += 1
        if (failFirstConfirm && confirms === 1) {
          return status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } })
        }
        return { ...CONTRACT, status: 'confirmed', version: 4 }
      },
    },
    {
      method: 'POST',
      match: /^\/sales\/contracts\/k1\/cancel$/,
      handler: () => ({ ...CONTRACT, status: 'cancelled', version: 4 }),
    },
  ])
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
    </Routes>,
    { route: '/sales/contracts/k1' },
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ContractDetailPage — one Idempotency-Key per submission (KAN-266)', () => {
  it('a confirm retried after a failure keeps its key, beside its If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirstConfirm: true })
    renderPage()

    const confirm = await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })
    await user.click(confirm)
    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.generic'))).toBeInTheDocument()
    await user.click(confirm)
    await waitFor(() => expect(backend.callsTo(/\/confirm$/, 'POST')).toHaveLength(2))

    const [first, retry] = backend.callsTo(/\/confirm$/, 'POST')
    expect(first.headers.get('Idempotency-Key')).toBeTruthy()
    expect(retry.headers.get('Idempotency-Key')).toBe(first.headers.get('Idempotency-Key'))
    expect(retry.headers.get('If-Match')).toBe('3')
  })

  it('the same screen confirming another contract after a failure uses a new key', async () => {
    // ADR-059: embedded, the screen stays mounted while its contract changes.
    // A key left by k1's failed confirm must not travel to k2's.
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/contracts\/(k1|k2)$/, handler: (req) => ({ ...CONTRACT, id: req.pathname.split('/')[3] }) },
      { match: /^\/sales\/contracts\/(k1|k2)\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/confirm$/,
        handler: () => status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } }),
      },
      { method: 'POST', match: /^\/sales\/contracts\/k2\/confirm$/, handler: () => ({ ...CONTRACT, id: 'k2', status: 'confirmed', version: 4 }) },
    ])
    function Host() {
      const [contractId, setContractId] = useState('k1')
      return (
        <>
          <button type="button" onClick={() => setContractId('k2')}>next contract</button>
          <ContractDetailContent contractId={contractId} embedded />
        </>
      )
    }
    renderWithProviders(<Host />)

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))
    await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.generic'))
    await user.click(screen.getByRole('button', { name: 'next contract' }))
    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k2$/, 'GET').length).toBeGreaterThan(0))
    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))
    await waitFor(() => expect(backend.callsTo(/k2\/confirm$/, 'POST')).toHaveLength(1))

    const [k1] = backend.callsTo(/k1\/confirm$/, 'POST')
    const [k2] = backend.callsTo(/k2\/confirm$/, 'POST')
    expect(k1.headers.get('Idempotency-Key')).toBeTruthy()
    expect(k2.headers.get('Idempotency-Key')).toBeTruthy()
    expect(k2.headers.get('Idempotency-Key')).not.toBe(k1.headers.get('Idempotency-Key'))
  })

  it('a cancel carries a key beside its If-Match', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'prompt').mockReturnValue('Kunde storniert.')
    const backend = install()
    renderPage()

    await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })
    await user.click(screen.getByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contractDetail.actions.cancel')) }))
    await waitFor(() => expect(backend.callsTo(/\/cancel$/, 'POST')).toHaveLength(1))

    const [cancel] = backend.callsTo(/\/cancel$/, 'POST')
    expect(cancel.headers.get('Idempotency-Key')).toBeTruthy()
    expect(cancel.headers.get('If-Match')).toBe('3')
    expect(cancel.body).toEqual({ reason: 'Kunde storniert.' })
  })
})
