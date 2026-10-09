// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-266 — the trade-in saved by VIN carries an Idempotency-Key beside its
// If-Match, so a save whose response was lost is replayed instead of
// answered with a version conflict. The valuation attach's key is pinned in
// OfferWorkspace.configurator.render.test.tsx, the one test that drives it.

const OFFER = {
  id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', customerId: null, customerLabel: null,
  vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
  tradeInLabel: null, tradeInVin: null, tradeInValuationId: null, tradeInValue: null, containers: [], version: 4,
} as unknown as SalesOfferRead

function install() {
  return installFakeBackend([
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    {
      method: 'POST',
      match: /^\/sales\/offers\/o1\/trade-in$/,
      handler: () => ({ ...OFFER, tradeInVehicleId: 'veh-1', tradeInLabel: 'VW Golf', version: 5 }),
    },
  ])
}

afterEach(() => cleanup())

describe('OfferWorkspace — the trade-in carries a key (KAN-266)', () => {
  it('a trade-in saved by VIN carries a key beside its If-Match', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o1' },
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
    await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel')), 'WVWZZZ1KZAW123456')
    await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.labelLabel')), 'VW Golf')
    await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(backend.callsTo(/\/trade-in$/, 'POST')).toHaveLength(1))

    const [save] = backend.callsTo(/\/trade-in$/, 'POST')
    expect(save.headers.get('Idempotency-Key')).toBeTruthy()
    expect(save.headers.get('If-Match')).toBe('4')
    expect(save.body).toMatchObject({ vin: 'WVWZZZ1KZAW123456', vehicleLabel: 'VW Golf' })
  })
})
