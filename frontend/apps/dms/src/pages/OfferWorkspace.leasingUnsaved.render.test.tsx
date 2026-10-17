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

// KAN-344 — KAN-327 made Continue wait for a price typed inside its 500 ms
// debounce, but the three leasing fields debounce the same way and were not
// part of that gate: a leasing value typed just before Continue was neither
// `pricingUnsaved` nor a queued save, so Generate could open before the
// leasing PATCH was sent (ADR-063: the reviewed offer is what was entered).

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

const LEASING_FIELDS = [
  { label: 'offerWorkspace.leasing.downPayment', typed: '5000', patch: { leasingDownPayment: 5000 } },
  { label: 'offerWorkspace.leasing.termMonths', typed: '48', patch: { leasingTermMonths: 48 } },
  { label: 'offerWorkspace.leasing.kmPerYear', typed: '15000', patch: { leasingKmPerYear: 15000 } },
] as const

describe('OfferWorkspace — Continue waits for a leasing edit still in its debounce (KAN-344)', () => {
  it.each(LEASING_FIELDS)('$label typed just before Continue is saved before Generate can open', async (field) => {
    let current = offerAt(1)
    const backend = installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/offers\/o1$/,
        handler: (req) => {
          current = { ...current, ...(req.body as object), version: current.version + 1 }
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

    const input = await screen.findByRole('textbox', { name: i18n.t(field.label) })
    fireEvent.change(input, { target: { value: field.typed } })
    // Within the debounce the typed value is unsaved: Continue waits for it.
    expect(continueButton).toBeDisabled()
    fireEvent.click(continueButton)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')[0].body).toMatchObject(field.patch)
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(i18n.t('offerWorkspace.generate.title'))
  })
})
