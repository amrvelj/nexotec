// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, within } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead, ValuationRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-101 — ADR-048 as amended: "a manual figure is marked manual and is
// never presented as a provider valuation". The trade-in card showed the
// value with no source at all.

const offerWithTradeIn = (): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0002',
  status: 'draft',
  customerId: 'cust-1',
  customerLabel: 'Hans Muster',
  customerLocality: null,
  vehicleSource: 'stock',
  stockItemId: 'st-1',
  vehicleLabel: 'Skoda Octavia 2.0 TDI',
  manualVehicleCondition: null,
  manualBasePrice: null,
  leasingDownPayment: null,
  leasingTermMonths: null,
  leasingKmPerYear: null,
  basePrice: '32000.00',
  optionsTotal: null,
  listPrice: '32000.00',
  accessoriesTotal: null,
  totalBeforeDiscount: '32000.00',
  discountType: null,
  discountValue: null,
  discountAmount: null,
  grossPrice: '32000.00',
  costBasis: null,
  margin: null,
  vehicleSnapshotFrozenAt: '2026-09-17T00:00:00Z',
  tradeInVehicleId: 'veh-9',
  tradeInLabel: 'VW Golf 1.5 TSI',
  tradeInVin: 'WVWZZZ1KZAW654321',
  tradeInValuationId: 'val-42',
  tradeInValue: '12000.00',
  tradeInPurchasePrice: '12000.00',
  payable: '20000.00',
  cancelledReason: null,
  copiedFromOfferId: null,
  containers: [
    { id: 'customer', requirement: 'required', status: 'complete' },
    { id: 'vehicle', requirement: 'required', status: 'complete' },
    { id: 'pricing', requirement: 'required', status: 'complete' },
    { id: 'trade_in', requirement: 'optional', status: 'complete' },
    { id: 'leasing', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: 'used',
  version: 1,
  createdAt: '2026-02-01T00:00:00Z',
  updatedAt: '2026-02-01T00:00:00Z',
})

// Only the fields the trade-in card reads; the page never renders the rest.
const valuation = (source: ValuationRead['source']) =>
  ({ id: 'val-42', valuationNumber: 'V-000042', source, finalOffer: '12000.00', status: 'valid' }) as ValuationRead

function installBackend(source: ValuationRead['source']) {
  return installFakeBackend([
    { match: /^\/sales\/offers\/o1$/, handler: () => offerWithTradeIn() },
    { match: /^\/valuations\/val-42$/, handler: () => valuation(source) },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: false }) },
    { match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
    { match: /^\/customers\/cust-1\/(phones|emails)$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1\/(vehicles|external-ids)$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/cust-1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

function renderWorkspace() {
  return renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
}

// OverviewCard: outer card > [title row > [label, badge], children] — two
// hops from the title text reach the card that holds the trade-in value.
const tradeInCard = async () =>
  (await screen.findByText(i18n.t('offerWorkspace.containers.tradeIn'))).parentElement!.parentElement!

describe('OfferWorkspace — the trade-in value carries its source (KAN-101)', () => {
  it('marks a manual valuation as manual next to the trade-in value', async () => {
    installBackend('manual')
    renderWorkspace()

    const card = await tradeInCard()
    expect(await within(card).findByText(i18n.t('valuationSource.manual'))).toBeInTheDocument()
  })

  it('marks an auto-i-dat valuation as auto-i-dat', async () => {
    installBackend('auto_i_dat')
    renderWorkspace()

    const card = await tradeInCard()
    expect(await within(card).findByText('auto-i-dat')).toBeInTheDocument()
    expect(within(card).queryByText(i18n.t('valuationSource.manual'))).not.toBeInTheDocument()
  })
})
