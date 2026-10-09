// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-266 — «Als verwendet markieren» carries an Idempotency-Key beside its
// If-Match, so a stamp whose response was lost is replayed instead of
// answered with a version conflict.

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: true,
  customerId: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null, vehicleVin: null,
  finalOffer: '12000.00', deductions: [], version: 2, validFrom: '2026-10-01', validUntil: '2026-10-31',
  vehicleFirstRegistration: null, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

function install() {
  return installFakeBackend([
    { method: 'GET', match: /^\/valuations\/val-1$/, handler: () => VALUATION },
    { method: 'POST', match: /^\/valuations\/val-1\/mark-used$/, handler: () => ({ ...VALUATION, status: 'used', version: 3 }) },
  ])
}

afterEach(() => cleanup())

describe('ValuationDetailPage — mark-used carries a key (KAN-266)', () => {
  it('«Als verwendet markieren» carries a key beside its If-Match', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(
      <Routes>
        <Route path="/valuations/:id" element={<ValuationDetailPage />} />
      </Routes>,
      { route: '/valuations/val-1' },
    )

    await screen.findByText('B-000001')
    await user.click(screen.getByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.markUsed')) }))
    await waitFor(() => expect(backend.callsTo(/\/mark-used$/, 'POST')).toHaveLength(1))

    const [stamp] = backend.callsTo(/\/mark-used$/, 'POST')
    expect(stamp.headers.get('Idempotency-Key')).toBeTruthy()
    expect(stamp.headers.get('If-Match')).toBe('2')
  })
})
