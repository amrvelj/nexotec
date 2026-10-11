// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-232 — the detail header's overflow `⋯` takes its accessible name from
// the caller's translation, never the kit's hardcoded English.

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: true,
  customerId: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null, vehicleVin: null,
  finalOffer: '12000.00', deductions: [], version: 2, validFrom: '2026-10-01', validUntil: '2026-10-31',
  vehicleFirstRegistration: null, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('DetailHeader overflow label (KAN-232)', () => {
  it('names the overflow button and its menu in French under the French UI', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([{ method: 'GET', match: /^\/valuations\/val-1$/, handler: () => VALUATION }])
    renderWithProviders(
      <Routes>
        <Route path="/valuations/:id" element={<ValuationDetailPage />} />
      </Routes>,
      { route: '/valuations/val-1' },
    )

    await screen.findByText('B-000001')
    const overflow = screen.getByRole('button', { name: "Plus d'actions" })
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument()

    await user.click(overflow)
    expect(await screen.findByRole('menu')).toBeInTheDocument()
  })
})
