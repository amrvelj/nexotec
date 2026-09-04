// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'
import { CustomerDetailPage } from './CustomerDetailPage'

// WP-6c exit criterion / ADR-059: "a customer opens as an overlay above
// another screen without the underlying screen losing state." The one the
// ADR is written for — "a seller thirty minutes into a negotiation who
// opens the customer … presses back and finds an empty draft has learned
// never to click that again." Here: a half-typed vehicle label on the
// offer workspace must still be there after the customer overlay opens,
// is used, and closes.

const draftOffer = (): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0001',
  status: 'draft',
  customerId: 'cust-1',
  customerLabel: 'Hans Muster',
  customerLocality: null,
  vehicleSource: null,
  stockItemId: null,
  vehicleLabel: null,
  manualVehicleCondition: null,
  manualBasePrice: null,
  leasingDownPayment: null,
  leasingTermMonths: null,
  leasingKmPerYear: null,
  basePrice: null,
  optionsTotal: null,
  listPrice: null,
  accessoriesTotal: null,
  totalBeforeDiscount: null,
  discountType: null,
  discountValue: null,
  discountAmount: null,
  grossPrice: null,
  costBasis: null,
  margin: null,
  vehicleSnapshotFrozenAt: null,
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
    { id: 'vehicle', requirement: 'required', status: 'not_started' },
    { id: 'pricing', requirement: 'required', status: 'not_started' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
    { id: 'leasing', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: null,
  version: 1,
  createdAt: '2026-02-01T00:00:00Z',
  updatedAt: '2026-02-01T00:00:00Z',
})

function LocationProbe() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

function installOfferBackend() {
  return installFakeBackend([
    { match: /^\/sales\/offers\/o1$/, handler: () => draftOffer() },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: false }) },
    { match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
    { match: /^\/customers\/cust-1\/(phones|emails)$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1\/(vehicles|external-ids)$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/cust-1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

function renderWorkspace() {
  return renderWithProviders(
    <>
      <LocationProbe />
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>
    </>,
    { route: '/sales/offers/o1' },
  )
}

const manualLabelPlaceholder = () => i18n.t('offerWorkspace.vehicle.manualLabelPlaceholder')

async function typeHalfBuiltVehicle(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.vehicle.configure') }))
  const field = screen.getByPlaceholderText(manualLabelPlaceholder())
  await user.type(field, 'Ferrari Testarossa 1987')
  return field
}

describe('OfferWorkspace — a customer overlay never disturbs the offer underneath (ADR-059)', () => {
  it('keeps the half-typed vehicle label through opening, using and closing the overlay', async () => {
    const user = userEvent.setup()
    installOfferBackend()
    renderWorkspace()

    await typeHalfBuiltVehicle(user)
    expect(screen.getByPlaceholderText(manualLabelPlaceholder())).toHaveValue('Ferrari Testarossa 1987')

    // Open the customer as an overlay.
    await user.click(screen.getByRole('button', { name: 'Hans Muster' }))
    const dialog = await screen.findByRole('dialog')
    // The address bar is untouched — the overlay is not a navigation.
    expect(screen.getByTestId('path')).toHaveTextContent('/sales/offers/o1')

    // Work inside the overlay: switch a tab (repaints the top layer only).
    const vehiclesTab = within(dialog).getByRole('tab', { name: new RegExp(i18n.t('customerDetail.tabs.vehicles'), 'i') })
    await user.click(vehiclesTab)
    await waitFor(() => expect(vehiclesTab).toHaveAttribute('aria-selected', 'true'))

    // Close it.
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // The offer workspace still holds exactly what was typed.
    expect(screen.getByPlaceholderText(manualLabelPlaceholder())).toHaveValue('Ferrari Testarossa 1987')
    expect(screen.getByTestId('path')).toHaveTextContent('/sales/offers/o1')
  })

  it('Escape closes the overlay and still leaves the offer state intact', async () => {
    const user = userEvent.setup()
    installOfferBackend()
    renderWorkspace()

    await typeHalfBuiltVehicle(user)
    await user.click(screen.getByRole('button', { name: 'Hans Muster' }))
    await screen.findByRole('dialog')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(screen.getByPlaceholderText(manualLabelPlaceholder())).toHaveValue('Ferrari Testarossa 1987')
  })
})
