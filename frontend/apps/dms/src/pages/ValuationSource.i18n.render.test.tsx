// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import de from '../i18n/locales/de.json'
import en from '../i18n/locales/en.json'
import fr from '../i18n/locales/fr.json'
import it_ from '../i18n/locales/it.json'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationsListPage } from './ValuationsListPage'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-110 — the ui-kit's ValuationSourceBadge used to carry the German
// "Manuell" as its default label, and the two valuation pages rendered it
// without a label, so a manual figure read "Manuell" under every UI
// language. Both pages now render the source through ValuationSourceMarker
// (KAN-101), the one translated marking, and the kit badge has no default.

// jsdom has no layout → TanStack Virtual renders zero rows (same mock as
// the other grid tests).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: null, customerLabel: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null, vehicleVin: null,
  mileage: 42000, finalOffer: '12000.00', deductions: [], version: 2, validFrom: '2026-10-01', validUntil: '2026-10-31',
  vehicleFirstRegistration: null, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

// Hardcoded, not derived via i18n.t(): the expected value must not be computed
// the way the component computes it (see IntegrationDealerView.categories).
const FRENCH_MANUAL = 'Manuel'
const GERMAN_MANUAL = 'Manuell'

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ValuationsListPage — the source column is translated (KAN-110)', () => {
  it('renders a manual valuation as "Manuel" under the French UI, never the German default', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { method: 'GET', match: /^\/valuations$/, handler: () => ({ items: [VALUATION], nextCursor: null, total: 1, totalIsEstimate: false }) },
    ])
    renderWithProviders(<ValuationsListPage />, { route: '/valuations' })

    await screen.findByText('B-000001')
    await waitFor(() => expect(screen.getByText(FRENCH_MANUAL)).toBeInTheDocument())
    expect(screen.queryByText(GERMAN_MANUAL)).not.toBeInTheDocument()
  })
})

describe('ValuationDetailPage — the source badge is translated (KAN-110)', () => {
  it('renders a manual valuation as "Manuel" under the French UI, never the German default', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([{ method: 'GET', match: /^\/valuations\/val-1$/, handler: () => VALUATION }])
    renderWithProviders(
      <Routes>
        <Route path="/valuations/:id" element={<ValuationDetailPage />} />
      </Routes>,
      { route: '/valuations/val-1' },
    )

    await screen.findByText('B-000001')
    expect(screen.getByText(FRENCH_MANUAL)).toBeInTheDocument()
    expect(screen.queryByText(GERMAN_MANUAL)).not.toBeInTheDocument()
  })
})

describe("Stock's Evaluation hint names the valuation module, not Sales (KAN-110, ADR-066)", () => {
  // The word each language uses for the Sales module in its own bundle
  // (`shell.nav.sales` / `salesList.title` territory), which the hint must
  // no longer claim owns valuations.
  it.each([
    ['de', de.stockDetail.evaluation.hint, /Verkauf/],
    ['en', en.stockDetail.evaluation.hint, /Sales module/],
    ['fr', fr.stockDetail.evaluation.hint, /module Vente/],
    ['it', it_.stockDetail.evaluation.hint, /modulo Vendite/],
  ])('%s: the hint does not say valuations live in the Sales module', (_lang, hint, salesWord) => {
    expect(hint).not.toMatch(salesWord)
  })
})
