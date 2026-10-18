// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { CustomerRead, SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-368 (ADR-061: a disabled action says why) — Continue was greyed out with
// no explanation while an edit was unsaved or saving, or the customer was
// do-not-contact: the footer's missing label names only incomplete sections.

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

function installBackend(customerOverrides: Partial<CustomerRead>, patch: () => unknown = () => offerAt(2)) {
  return installFakeBackend([
    { method: 'PATCH', match: /^\/sales\/offers\/o1$/, handler: patch },
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => offerAt(1) },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: true }) },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    {
      match: /^\/customers\/cust-1$/,
      handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster', ...customerOverrides }),
    },
  ])
}

const MARKER = /MISSING I18N KEY/

async function hoverContinue() {
  const button = await screen.findByRole('button', { name: i18n.t('offerWorkspace.continue') })
  await userEvent.hover(button.parentElement as HTMLElement)
  return button
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('OfferWorkspace — a disabled Continue says why (KAN-368)', () => {
  it('names a do-not-contact customer, in fr', async () => {
    await i18n.changeLanguage('fr')
    installBackend({ lifecycleStatus: 'do_not_contact' })
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o1' },
    )

    await waitFor(async () => expect(await screen.findByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeDisabled())
    await hoverContinue()

    expect(await screen.findByText(i18n.t('offerWorkspace.continueDisabled.doNotContact'))).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(MARKER)
  })

  it('names an unsaved edit, then the save still in flight, in fr', async () => {
    await i18n.changeLanguage('fr')
    let releasePatch: (value: unknown) => void = () => {}
    const backend = installBackend({}, () => new Promise((resolve) => (releasePatch = resolve)))
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o1' },
    )

    const continueButton = () => screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })
    await waitFor(() => expect(continueButton()).toBeEnabled())
    const reason = i18n.t('offerWorkspace.continueDisabled.saving')

    // Inside the leasing field's debounce: unsaved.
    fireEvent.change(await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.leasing.termMonths') }), {
      target: { value: '48' },
    })
    expect(continueButton()).toBeDisabled()
    await hoverContinue()
    expect(await screen.findByText(reason)).toBeInTheDocument()

    // The PATCH is sent and held: a save in flight says the same.
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(continueButton()).toBeDisabled()
    await userEvent.unhover(continueButton().parentElement as HTMLElement)
    await hoverContinue()
    expect(await screen.findByText(reason)).toBeInTheDocument()

    releasePatch(offerAt(2, { leasingTermMonths: 48 }))
    await waitFor(() => expect(continueButton()).toBeEnabled())
  })
})
