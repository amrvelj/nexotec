// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-189 — a trade-in recorded by VIN creates no vehicle-mdm record any
// more (the car enters the register when it is bought, ADR-103), so the deal
// carries `tradeInVin` with `tradeInVehicleId: null`. The trade-in card must
// still show it as recorded, not offer «add» again.

const OFFER = {
  id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', customerId: null, customerLabel: null,
  vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
  tradeInLabel: 'Seat Leon 1.5 TSI', tradeInVin: 'WVWZZZ1KZAW189004', tradeInValuationId: null, tradeInValue: null,
  containers: [], version: 4,
} as unknown as SalesOfferRead

afterEach(() => cleanup())

describe('the offer workspace — a VIN-only trade-in (KAN-189)', () => {
  it('shows the recorded trade-in instead of the add action', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
      { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
      { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o1' },
    )

    expect(await screen.findByText('Seat Leon 1.5 TSI')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') })).toBeNull()
  })
})
