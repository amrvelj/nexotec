// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import i18n from '../i18n'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import { CustomerDetailPage } from './CustomerDetailPage'
import type { SalesContractRead, SalesDealRead, SalesOfferRead, ValuationRead } from '../api/types'
import { SalesListPage } from './SalesListPage'
import { ValuationsListPage } from './ValuationsListPage'

// KAN-334 — ValuationStatusBadge and SalesTypeBadge were rendered without a
// label, so their German kit defaults (Gültig, Offerte, …) showed in every
// UI language. The kit badges now take a required label and the screens pass
// the translation (valuationEnums.status.*, salesEnums.entityType.*).

// jsdom has no layout: render every row (the shim the columns tests use).
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

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const NOW = '2026-10-01T09:00:00Z'
const page = <T,>(items: T[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

describe('Kit status badges speak the UI language (KAN-334)', () => {
  it('ValuationStatusBadge on the valuation list: French status, never the German kit default', async () => {
    await i18n.changeLanguage('fr')
    const valuation = (id: string, number: string, status: string) =>
      ({
        id, valuationNumber: number, status, source: 'manual', hasSignedContract: false, customerId: null,
        customerLabel: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null, vehicleVin: null,
        finalOffer: '12000.00', deductions: [], version: 1, validFrom: '2026-10-01', validUntil: '2026-10-31',
        createdAt: NOW, updatedAt: NOW,
      }) as unknown as ValuationRead
    installFakeBackend([
      {
        match: /^\/valuations$/,
        handler: () => page([valuation('v1', 'B-000001', 'valid'), valuation('v2', 'B-000002', 'expired')]),
      },
    ])
    renderWithProviders(<ValuationsListPage />, { route: '/valuations' })

    // Hardcoded expectations, not i18n.t(): a key missing on both sides must fail here.
    expect(within(await screen.findByRole('row', { name: /B-000001/ })).getByText('Valide')).toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /B-000002/ })).getByText('Expirée')).toBeInTheDocument()
    expect(screen.queryByText('Gültig')).toBeNull()
    expect(screen.queryByText('Abgelaufen')).toBeNull()
  })

  it('SalesTypeBadge on the deals list: French entity type, never the German kit default', async () => {
    await i18n.changeLanguage('fr')
    const deal = (id: string, number: string, entityType: 'offer' | 'contract', status: string) =>
      ({
        id, entityType, number, status, offerId: null, offerNumber: null, contractId: null, contractNumber: null,
        customerId: null, customerLabel: 'Muster AG', customerLocality: 'Bern', vehicleLabel: 'VW Golf GTI',
        grossPrice: '42500.00', margin: null, documentsCount: 0, createdAt: NOW, updatedAt: NOW,
      }) as unknown as SalesDealRead
    installFakeBackend([
      {
        match: /^\/sales\/deals$/,
        handler: () => page([deal('d1', 'O-000001', 'offer', 'draft'), deal('d2', 'V-000001', 'contract', 'confirmed')]),
      },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })

    expect(within(await screen.findByRole('row', { name: /O-000001/ })).getByText('Offre')).toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /V-000001/ })).getByText('Contrat')).toBeInTheDocument()
    expect(screen.queryByText('Offerte')).toBeNull()
    expect(screen.queryByText('Vertrag')).toBeNull()
  })

  it("SalesTypeBadge on the customer's Offers & contracts tab: French entity type", async () => {
    await i18n.changeLanguage('fr')
    const offer = {
      id: 'o1', offerNumber: 'O-000334', status: 'open', customerId: 'c1', customerLabel: 'Anna Muster',
      vehicleLabel: 'VW Golf', grossPrice: null, createdAt: NOW, updatedAt: NOW,
    } as unknown as SalesOfferRead
    const contract = {
      id: 'k1', contractNumber: 'V-000334', status: 'confirmed', offerId: null, customerId: 'c1',
      customerLabel: 'Anna Muster', vehicleLabel: 'Audi A3', grossPrice: null, createdAt: NOW, updatedAt: NOW,
    } as unknown as SalesContractRead
    installFakeBackend([
      { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
      { match: /^\/customers\/c1\/(phones|emails)$/, handler: () => ({ items: [] }) },
      { match: /^\/customers\/c1\/(vehicles|external-ids|audit-log)$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'GET', match: /^\/sales\/offers$/, handler: () => page([offer]) },
      { method: 'GET', match: /^\/sales\/contracts$/, handler: () => page([contract]) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1?tab=offersContracts' },
    )

    expect(within(await screen.findByRole('row', { name: /O-000334/ })).getByText('Offre')).toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /V-000334/ })).getByText('Contrat')).toBeInTheDocument()
    expect(screen.queryByText('Offerte')).toBeNull()
    expect(screen.queryByText('Vertrag')).toBeNull()
  })
})
