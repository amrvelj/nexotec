// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

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

// KAN-480 — a lone «-» (the start of a negative figure, or a stray key) is
// not a number: Mantine hands it over as a string the hosts turn into NaN,
// and JSON.stringify(NaN) is null. The debounced field committed it, so the
// saved discount was cleared on the server 500 ms later. A value that is not
// a finite number is now kept in the field, marked as not valid, and never
// committed; it counts as unsaved (KAN-288).

function renderWorkspace() {
  return renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
}

function backendFor(start: SalesOfferRead) {
  let current = start
  return installFakeBackend([
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
}

const pause = () => new Promise((resolve) => setTimeout(resolve, 800))

describe('OfferWorkspace — a value that is not a number is never saved (KAN-480)', () => {
  it('typing «-» into the discount sends no PATCH; the field keeps it, marked not valid, and Continue waits', async () => {
    const backend = backendFor(
      offerAt(1, { discountValue: '500.00', discountAmount: '500.00', grossPrice: '29500.00' }),
    )
    renderWorkspace()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '-' } })
    await pause()

    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)
    expect((discount as HTMLInputElement).value).toContain('-')
    expect(discount).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(i18n.t('common.notYetANumber'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeDisabled()
  })

  // KAN-493: «-200» is a number but a negative discount (the API's ge=0,
  // KAN-482), so finishing the figure means a figure that is not negative.
  it('finishing the figure saves it: «-», «-200», then «200» sends 200 once', async () => {
    const backend = backendFor(offerAt(1, { discountValue: '500.00' }))
    renderWorkspace()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '-' } })
    await pause()
    fireEvent.change(discount, { target: { value: '-200' } })
    await pause()
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)
    fireEvent.change(discount, { target: { value: '200' } })
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')[0].body).toMatchObject({ discountValue: 200 })
    expect(discount).not.toHaveAttribute('aria-invalid', 'true')
  })

  it('leaving the field on a lone «-» keeps it: the input\'s own blur tidy-up is not a clear', async () => {
    const backend = backendFor(offerAt(1, { discountValue: '500.00' }))
    renderWorkspace()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.focus(discount)
    fireEvent.change(discount, { target: { value: '-' } })
    fireEvent.blur(discount)
    await pause()

    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)
    expect((discount as HTMLInputElement).value).toContain('-')
    expect(discount).toHaveAttribute('aria-invalid', 'true')
  })

  it('deleting the «-» empties the field on purpose: that clear is saved', async () => {
    const backend = backendFor(offerAt(1, { discountValue: '500.00' }))
    renderWorkspace()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '-' } })
    fireEvent.change(discount, { target: { value: '' } })
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')[0].body).toMatchObject({ discountValue: null })
  })

  it('changing the discount type while the value is «-» sends the saved discount, never null', async () => {
    const backend = backendFor(offerAt(1, { discountValue: '500.00' }))
    const user = userEvent.setup()
    renderWorkspace()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '-' } })
    await user.click(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountType') }))
    await user.click(await screen.findByRole('option', { name: i18n.t('offerWorkspace.pricing.discountPercent') }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')[0].body).toEqual({ discountType: 'percent', discountValue: 500 })
  })

  it('the leasing down payment keeps a lone «-» too', async () => {
    const backend = backendFor(offerAt(1, { leasingDownPayment: '5000.00' }))
    renderWorkspace()

    const downPayment = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.leasing.downPayment') })
    fireEvent.change(downPayment, { target: { value: '-' } })
    await pause()
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)
    expect(downPayment).toHaveAttribute('aria-invalid', 'true')
  })
})

// KAN-493 — the API refuses a negative base price, discount or leasing figure
// (KAN-482); the five inputs now never send one. The input still takes «-»
// (one that refused it would empty itself when its text is replaced by «-»,
// and that clear would be saved — KAN-480's hazard): a negative figure is
// kept as typed, marked «Darf nicht negativ sein», never committed, and
// Continue waits for it.
describe('OfferWorkspace — a negative figure is never sent (KAN-493)', () => {
  it.each([
    ['offerWorkspace.pricing.discountValue', { discountValue: '500.00' }],
    ['offerWorkspace.leasing.downPayment', { leasingDownPayment: '5000.00' }],
    ['offerWorkspace.leasing.termMonths', { leasingTermMonths: 48 }],
    ['offerWorkspace.leasing.kmPerYear', { leasingKmPerYear: 15000 }],
    ['offerWorkspace.pricing.manualBasePrice', { vehicleSource: 'manual', manualBasePrice: '25000.00' }],
  ] as const)('%s: «-12» sends no PATCH, says why, and Continue waits', async (labelKey, overrides) => {
    const backend = backendFor(offerAt(1, overrides as Partial<SalesOfferRead>))
    renderWorkspace()

    const field = await screen.findByRole('textbox', { name: i18n.t(labelKey) })
    fireEvent.change(field, { target: { value: '-12' } })
    await pause()

    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)
    expect((field as HTMLInputElement).value).toMatch(/^-.*12$/) // CHF inputs read «-CHF 12»
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(i18n.t('common.notNegative'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })).toBeDisabled()
  })

  it('in French, the reason is French', async () => {
    await i18n.changeLanguage('fr')
    try {
      backendFor(offerAt(1, { leasingTermMonths: 48 }))
      renderWorkspace()
      const field = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.leasing.termMonths') })
      fireEvent.change(field, { target: { value: '-1' } })
      expect(await screen.findByText('Ne peut pas être négatif')).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
