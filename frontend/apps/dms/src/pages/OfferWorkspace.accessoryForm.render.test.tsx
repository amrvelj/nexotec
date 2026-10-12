// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-323 — adding an accessory cleared the typed form before the save had
// landed. A refused save (said on screen since KAN-314) then looked like a
// save and lost what the seller typed — ADR-035: a failed save must not
// look like a save and must not discard input.

const offerAt = (version: number, overrides: Partial<SalesOfferRead> = {}): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0323',
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
  discountType: null,
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

const accessory = (id: string, label: string, unitPrice: string) => ({
  id, kind: 'accessory', code: id.toUpperCase(), label, unitPrice, quantity: 1, included: true,
  discountType: null, discountValue: null, discountResolvedAmount: null, discountSuppressedReason: null, position: 1,
})

function installBackend(routes: FakeRoute[]) {
  return installFakeBackend([
    ...routes,
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => offerAt(1) },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: true }) },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
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

async function typeAccessory(user: ReturnType<typeof userEvent.setup>) {
  const label = await screen.findByLabelText(i18n.t('offerWorkspace.lineItems.accessoryLabel'))
  await user.type(label, 'Fussmatten')
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryPrice')), '120')
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryDiscount')), '10')
  await user.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.lineItems.add') }))
}

describe('OfferWorkspace — the accessory form keeps its values until the save lands (KAN-323)', () => {
  it('a refused save says so and leaves the typed accessory in the form', async () => {
    const user = userEvent.setup()
    const backend = installBackend([
      {
        method: 'PUT',
        match: /^\/sales\/offers\/o1\/line-items$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'SalesOffer has been modified since If-Match version 1 (current version is 2).',
              details: { currentVersion: 2, ifMatchVersion: 1 },
            },
          }),
      },
    ])
    renderWorkspace()

    await typeAccessory(user)
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(1))
    expect(await screen.findByText(i18n.t('offerWorkspace.errors.saveRefused.staleVersion'))).toBeInTheDocument()

    expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryLabel'))).toHaveValue('Fussmatten')
    expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryPrice'))).toHaveValue('120')
    expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryDiscount'))).toHaveValue('10')
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.lineItems.add') })).toBeEnabled()
  })

  it('a save that lands clears the form', async () => {
    const user = userEvent.setup()
    let serverLines: ReturnType<typeof accessory>[] = []
    const backend = installBackend([
      {
        method: 'PUT',
        match: /^\/sales\/offers\/o1\/line-items$/,
        handler: (req) => {
          const sent = (req.body as { accessories: Array<{ label: string; unitPrice: string }> }).accessories
          serverLines = sent.map((a, i) => accessory(`a${i + 1}`, a.label, a.unitPrice))
          return offerAt(2, { accessoriesTotal: '120.00', grossPrice: '30120.00' })
        },
      },
      { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: serverLines }) },
    ])
    renderWorkspace()

    await typeAccessory(user)
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(1))
    expect((backend.callsTo(/\/line-items$/, 'PUT')[0].body as { accessories: unknown[] }).accessories).toEqual([
      expect.objectContaining({ label: 'Fussmatten', unitPrice: '120', discountType: 'amount', discountValue: '10' }),
    ])

    expect(await screen.findByText('Fussmatten')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryLabel'))).toHaveValue(''))
    expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryPrice'))).toHaveValue('')
    expect(screen.getByLabelText(i18n.t('offerWorkspace.lineItems.accessoryDiscount'))).toHaveValue('')
    expect(screen.queryByText(i18n.t('offerWorkspace.errors.saveRefused.staleVersion'))).not.toBeInTheDocument()
  })
})
