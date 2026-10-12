// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-314 — since KAN-311 every route that changes an offer locks its row
// and checks If-Match, so two saves sent with the same version cannot both
// land: the second is a stale-version 409. The workspace fired each
// container's autosave on its own, with the version its render held, and
// showed nothing when one was refused.

const OPTION = {
  id: 'li-1', kind: 'factory_option', code: 'LED', label: 'LED-Scheinwerfer', unitPrice: '800.00', quantity: 1,
  included: true, discountType: null, discountValue: null, discountResolvedAmount: null,
  discountSuppressedReason: null, position: 0,
}

const offerAt = (version: number, overrides: Partial<SalesOfferRead> = {}): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0314',
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
  optionsTotal: '800.00',
  listPrice: '30800.00',
  accessoriesTotal: null,
  totalBeforeDiscount: '30800.00',
  discountType: null,
  discountValue: null,
  discountAmount: null,
  grossPrice: '30800.00',
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

function installBackend(routes: FakeRoute[]) {
  return installFakeBackend([
    ...routes,
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => offerAt(1) },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: true }) },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [OPTION] }) },
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

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('OfferWorkspace — overlapping autosaves are sent one after the other (KAN-314)', () => {
  it('a second container save waits for the first and carries the version it returned', async () => {
    const user = userEvent.setup()
    let releasePut: (value: unknown) => void = () => {}
    const backend = installBackend([
      {
        method: 'PUT',
        match: /^\/sales\/offers\/o1\/line-items$/,
        // Held in flight until the test releases it.
        handler: () => new Promise((resolve) => (releasePut = resolve)),
      },
      {
        method: 'PATCH',
        match: /^\/sales\/offers\/o1$/,
        handler: (req) => offerAt(Number(req.headers.get('If-Match')) + 1, { leasingTermMonths: 48 }),
      },
    ])
    renderWorkspace()

    // Save 1 — Preisaufbau: deselect a factory option (line items PUT).
    await user.click(await screen.findByRole('checkbox', { name: 'LED-Scheinwerfer' }))
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(1))
    expect(backend.callsTo(/\/line-items$/, 'PUT')[0].headers.get('If-Match')).toBe('1')

    // Save 2 — Leasing: a term typed while save 1 is still on its way
    // (the field commits after its 500 ms debounce).
    await user.type(screen.getByLabelText(i18n.t('offerWorkspace.leasing.termMonths')), '48')
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(0)

    releasePut(offerAt(2, { optionsTotal: '0.00', grossPrice: '30000.00' }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')
    expect(patch.headers.get('If-Match')).toBe('2')
    expect(patch.body).toEqual({ leasingTermMonths: 48 })

    // In order: the PUT reached the server before the PATCH.
    const writes = backend.calls.filter((c) => c.method === 'PUT' || c.method === 'PATCH').map((c) => c.method)
    expect(writes).toEqual(['PUT', 'PATCH'])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('a line-item save queued behind another is built from the lines the first one left', async () => {
    // Accessories are a full replace. Removing two quickly: the second
    // removal must not resend the list the first one just shortened.
    const user = userEvent.setup()
    const accessory = (id: string, label: string) => ({
      id, kind: 'accessory', code: id.toUpperCase(), label, unitPrice: '100.00', quantity: 1, included: true,
      discountType: null, discountValue: null, discountResolvedAmount: null, discountSuppressedReason: null, position: 1,
    })
    let serverLines = [accessory('a1', 'Fussmatten'), accessory('a2', 'Dachträger')]
    let version = 1
    let releaseFirst: () => void = () => {}
    const backend = installBackend([
      {
        method: 'PUT',
        match: /^\/sales\/offers\/o1\/line-items$/,
        handler: (req) => {
          const apply = () => {
            const kept = new Set((req.body as { accessories: Array<{ id?: string }> }).accessories.map((a) => a.id))
            serverLines = serverLines.filter((l) => kept.has(l.id))
            version += 1
            return offerAt(version)
          }
          if (version === 1) return new Promise((resolve) => (releaseFirst = () => resolve(apply())))
          return apply()
        },
      },
      { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: serverLines }) },
    ])
    renderWorkspace()

    await screen.findByText('Fussmatten')
    const [removeFirst, removeSecond] = screen.getAllByRole('button', { name: i18n.t('common.remove') })
    await user.click(removeFirst)
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(1))
    await user.click(removeSecond)
    releaseFirst()

    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(2))
    const [first, second] = backend.callsTo(/\/line-items$/, 'PUT')
    expect((first.body as { accessories: Array<{ id: string }> }).accessories.map((a) => a.id)).toEqual(['a2'])
    expect(second.headers.get('If-Match')).toBe('2')
    expect((second.body as { accessories: unknown[] }).accessories).toEqual([])
  })

  it('a refused save is said in the UI language, not the server\'s English', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const backend = installBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/offers\/o1$/,
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

    await user.type(await screen.findByLabelText(i18n.t('offerWorkspace.leasing.termMonths')), '48')

    const alert = await screen.findByRole('alert', {}, { timeout: 3000 })
    expect(alert).toHaveTextContent(i18n.getFixedT('fr')('offerWorkspace.errors.saveRefused.staleVersion'))
    expect(alert).not.toHaveTextContent('has been modified since')
    // The offer is reloaded so the screen shows what it now is.
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'GET').length).toBeGreaterThanOrEqual(2))
  })
})
