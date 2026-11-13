// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, within } from '@testing-library/react'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend } from '../../../test/fakeBackend'
import { formatCurrencyChf } from '../../../utils/format'
import type { ValuationRead } from '../../../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-552 — a valuation built on an auto-i-dat quote shows the quote (the
// three prices and the residual table); an advisor's own figure keeps the
// provider's beside it, marked manual.

const QUOTE = {
  id: 'quote-1', configurationId: null, fzKey: 'FZ100001', modelYear: 2020, firstRegistration: '2020-03-02',
  mileage: 41000, exchangePrice: '18400.00', purchasePrice: '17100.00', salePrice: '21700.00',
  vehicleExchangePrice: null, optionsExchangePrice: null, kilometreCorrectionExchange: null, kilometreExpected: null,
  exchangeRangeFrom: null, exchangeRangeTo: null, providerManual: false, screenMessages: [],
  residualTableState: 'present', providerRequestRef: 'ref-1', fetchedAt: '2026-11-09T07:00:00Z',
  residualTable: [{ durationMonths: 36, kmPerYear: 15000, totalPercent: '51.1', totalPrice: '14800.00' }],
}

const valuation = (overrides: Record<string, unknown>) =>
  ({
    id: 'val-1', valuationNumber: 'B-000001', status: 'valid', hasSignedContract: false, customerId: null,
    vehicleId: null, vehicleMake: 'Alfa Romeo', vehicleModel: 'Giulietta', vehicleTrim: null, vehicleVin: 'ZARFAMBN5L7552001',
    finalOffer: '17000.00', deductions: [], version: 1, validFrom: '2026-11-09', validUntil: '2026-12-09T08:00:00Z',
    vehicleFirstRegistration: '2020-03-02', mileage: 41000, configurationId: null, createdAt: '2026-11-09T08:00:00Z',
    updatedAt: '2026-11-09T08:00:00Z', providerQuote: QUOTE, ...overrides,
  }) as unknown as ValuationRead

function render(body: ValuationRead) {
  installFakeBackend([{ method: 'GET', match: /^\/valuations\/val-1$/, handler: () => body }])
  renderWithProviders(
    <Routes>
      <Route path="/valuations/:id" element={<ValuationDetailPage />} />
    </Routes>,
    { route: '/valuations/val-1' },
  )
}

describe('ValuationDetailPage — the auto-i-dat quote (KAN-552)', () => {
  it('shows the quote card with the three prices and the residual table', async () => {
    render(valuation({ source: 'auto_i_dat', providerValue: '18400.00' }))

    const card = await screen.findByTestId('provider-quote')
    expect(within(card).getByText(formatCurrencyChf(17100))).toBeInTheDocument()
    expect(within(card).getByText(formatCurrencyChf(21700))).toBeInTheDocument()
    expect(within(card).getByRole('table', { name: i18n.t('valuationQuote.residualTitle') })).toBeInTheDocument()
    expect(screen.getByText(i18n.t('valuationSource.auto_i_dat'))).toBeInTheDocument()
  })

  it("keeps the provider's figure beside an advisor's own, marked manual", async () => {
    render(valuation({ source: 'manual', providerValue: '16500.00' }))

    await screen.findByTestId('provider-quote')
    expect(screen.getByText(formatCurrencyChf(16500))).toBeInTheDocument()
    expect(screen.getAllByText(i18n.t('valuationQuote.exchange')).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(i18n.t('valuationSource.manual'))).toBeInTheDocument()
  })
})

// KAN-333's rule for the quote's date-only first registration: the recorded
// day, west of Greenwich too.
describe('ValuationDetailPage — the quote keeps its first-registration day (KAN-552)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders the valued first registration as the recorded day', async () => {
    render(valuation({ source: 'auto_i_dat', providerValue: '18400.00' }))

    const card = await screen.findByTestId('provider-quote')
    expect(card.textContent).toContain('02.03.2020')
    expect(card.textContent).not.toContain('01.03.2020')
  })
})
