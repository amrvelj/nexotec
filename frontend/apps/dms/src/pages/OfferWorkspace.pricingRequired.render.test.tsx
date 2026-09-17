// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-64 — PREISAUFBAU never carried an "Erforderlich" badge like
// KUNDE/FAHRZEUG, even though the backend's own containers array already
// treats it as required and the confirm step (services/offer.py) rejects
// anything short of status "complete". The frontend's own missing-container
// check only looked for "not_started", so a vehicle-selected-but-unpriced
// offer (status "in_progress") slipped through: "Offerte generieren" stayed
// enabled, and the user only learned pricing was a problem after building
// and previewing a whole document, via a generic backend error.

const baseOffer = (): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0002',
  status: 'draft',
  customerId: 'cust-1',
  customerLabel: 'Hans Muster',
  customerLocality: null,
  vehicleSource: 'stock',
  stockItemId: 'st-1',
  vehicleLabel: 'VW Golf 2019',
  manualVehicleCondition: null,
  manualBasePrice: null,
  leasingDownPayment: null,
  leasingTermMonths: null,
  leasingKmPerYear: null,
  basePrice: null,
  optionsTotal: null,
  listPrice: '0.00',
  accessoriesTotal: null,
  totalBeforeDiscount: '0.00',
  discountType: null,
  discountValue: null,
  discountAmount: null,
  grossPrice: '0.00',
  costBasis: null,
  margin: null,
  vehicleSnapshotFrozenAt: '2026-09-17T00:00:00Z',
  tradeInVehicleId: null,
  tradeInLabel: null,
  tradeInVin: null,
  tradeInValuationId: null,
  tradeInValue: null,
  tradeInPurchasePrice: null,
  payable: null,
  cancelledReason: null,
  copiedFromOfferId: null,
  containers: [
    { id: 'customer', requirement: 'required', status: 'complete' },
    { id: 'vehicle', requirement: 'required', status: 'complete' },
    { id: 'pricing', requirement: 'required', status: 'in_progress' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
    { id: 'leasing', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: 'used',
  version: 1,
  createdAt: '2026-02-01T00:00:00Z',
  updatedAt: '2026-02-01T00:00:00Z',
})

function installBackend(offer: SalesOfferRead) {
  return installFakeBackend([
    { match: /^\/sales\/offers\/o1$/, handler: () => offer },
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

// OverviewCard's DOM shape: outer card div > [title-row div > [label div, badge], children div].
// The title text sits in the innermost label div, so two parentElement hops
// reach the outer card that also contains the children (Alert, PriceBuildUp).
const pricingCard = async () =>
  (await screen.findByText(i18n.t('offerWorkspace.containers.pricing'))).parentElement!.parentElement!

describe('OfferWorkspace — Preisaufbau is flagged required exactly like Kunde/Fahrzeug (KAN-64)', () => {
  it('shows the Erforderlich badge and an honest no-price message when the vehicle has no usable price', async () => {
    installBackend(baseOffer())
    renderWorkspace()

    const card = await pricingCard()
    expect(within(card).getByText(i18n.t('offerWorkspace.requirement.required'))).toBeInTheDocument()
    expect(within(card).getByText(i18n.t('offerWorkspace.pricing.noPriceTitle'))).toBeInTheDocument()
    expect(within(card).getByText(i18n.t('offerWorkspace.pricing.noPriceBody'))).toBeInTheDocument()
  })

  it('disables "Offerte generieren" while pricing is unresolved, and names it in the missing list', async () => {
    installBackend(baseOffer())
    renderWorkspace()

    await pricingCard()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeDisabled()
    // The sticky footer's own missing-requirements line, not just the card's title.
    expect(
      screen.getByText(new RegExp(`${i18n.t('offerWorkspace.missing')}.*${i18n.t('offerWorkspace.containers.pricing')}`)),
    ).toBeInTheDocument()
  })

  it('offers a link to the stock record, which opens it as an overlay (ADR-059)', async () => {
    const user = userEvent.setup()
    installBackend(baseOffer())
    renderWorkspace()

    const card = await pricingCard()
    await user.click(within(card).getByRole('button', { name: i18n.t('offerWorkspace.pricing.noPriceLink') }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('carries no badge and no warning once a real base price exists', async () => {
    const priced = baseOffer()
    priced.basePrice = '32000.00'
    priced.listPrice = '32000.00'
    priced.grossPrice = '32000.00'
    priced.totalBeforeDiscount = '32000.00'
    priced.containers = (priced.containers ?? []).map((c) => (c.id === 'pricing' ? { ...c, status: 'complete' } : c))
    installBackend(priced)
    renderWorkspace()

    const card = await pricingCard()
    expect(within(card).queryByText(i18n.t('offerWorkspace.pricing.noPriceTitle'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).not.toBeDisabled()
  })
})
