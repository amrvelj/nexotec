// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-333 — the valued car's first registration is a date-only value;
// formatDate rendered it a day early anywhere west of Greenwich.

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: null, vehicleId: null, vehicleMake: 'Porsche', vehicleModel: '911', vehicleTrim: null, vehicleVin: null,
  finalOffer: '62000.00', deductions: [], version: 1, validFrom: '2026-10-01T08:00:00Z',
  validUntil: '2026-10-31T08:00:00Z', vehicleFirstRegistration: '1975-06-01', createdAt: '2026-10-01T08:00:00Z',
  updatedAt: '2026-10-01T08:00:00Z',
} as unknown as ValuationRead

describe('ValuationDetailPage — the first registration keeps its day west of Greenwich (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders the vehicle card first registration as the recorded day', async () => {
    installFakeBackend([{ method: 'GET', match: /^\/valuations\/val-1$/, handler: () => VALUATION }])
    renderWithProviders(
      <Routes>
        <Route path="/valuations/:id" element={<ValuationDetailPage />} />
      </Routes>,
      { route: '/valuations/val-1' },
    )

    await screen.findByText('B-000001')
    expect(screen.getByText('01.06.1975')).toBeInTheDocument()
    expect(screen.queryByText('31.05.1975')).not.toBeInTheDocument()
  })
})
