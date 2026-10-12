// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-327 — Continue waited for queued saves (KAN-314), but a price typed
// inside useDebouncedNumberField's 500 ms window is not yet a save, and
// the field does not flush on blur: typing a discount and clicking Continue
// at once opened Generate on the offer without it. ADR-063: the reviewed
// document must be what was entered. The contract page already waits for
// the same state (pricingUnsaved, KAN-288); the offer workspace now does.

const offerAt = (version: number, overrides: Partial<SalesOfferRead> = {}): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0327',
  status: 'draft',
  customerId: 'cust-1',
  customerLabel: 'Hans Muster',
  customerLocality: null,
  vehicleSource: 'stock',
  stockItemId: 'st-1',
  vehicleLabel: 'VW Golf 2024',
  manualVehicleCondition: null,
  manualBasePrice: null,
  leasingDownPayment: null,
  leasingTermMonths: null,
  leasingKmPerYear: null,
  basePrice: '30000.00',
  optionsTotal: null,
  listPrice: '30000.00',
  accessoriesTotal: null,
  totalBeforeDiscount: '30000.00',
  discountType: 'amount',
  discountValue: null,
  discountAmount: null,
  grossPrice: '30000.00',
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
    { id: 'pricing', requirement: 'required', status: 'complete' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
    { id: 'leasing', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: 'new',
  version,
  createdAt: '2026-02-01T00:00:00Z',
  updatedAt: '2026-02-01T00:00:00Z',
  ...overrides,
})

function renderWorkspace() {
  return renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
}

describe('OfferWorkspace — Continue waits for a price edit still in its debounce (KAN-327)', () => {
  it('a discount typed just before Continue is saved first; Generate opens on the offer that has it', async () => {
    let current = offerAt(1)
    const backend = installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/offers\/o1$/,
        handler: (req) => {
          current = { ...current, ...(req.body as object), discountAmount: '1000.00', grossPrice: '29000.00', version: current.version + 1 }
          return current
        },
      },
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => current },
      { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: true }) },
      { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
      { match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
    ])
    renderWorkspace()

    const continueButton = await screen.findByRole('button', { name: i18n.t('offerWorkspace.continue') })
    expect(continueButton).toBeEnabled()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '1000' } })
    // Within the debounce the typed figure is unsaved: Continue waits for it.
    expect(continueButton).toBeDisabled()
    fireEvent.click(continueButton)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')[0].body).toMatchObject({ discountType: 'amount', discountValue: 1000 })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeEnabled())
    // The build-up shows the saved discount before Generate can open.
    expect(screen.getByText("− CHF 1'000.00")).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(i18n.t('offerWorkspace.generate.title'))
    expect(backend.callsTo(/\/documents$/, 'POST')).toHaveLength(0)
  })
})
