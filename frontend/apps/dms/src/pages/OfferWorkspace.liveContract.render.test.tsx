// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-426 — since KAN-408 every write on an offer with a live contract is
// refused 409 `offer_has_contract`. The draft workspace now reads the
// contract from `OfferRead` (KAN-492) and renders the offer read-only with
// the way to the contract, before the first keystroke; a container whose save
// is refused that way says the same sentence.

const offer = (overrides: Partial<SalesOfferRead> = {}) =>
  ({
    id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', customerId: null, customerLabel: null,
    vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
    tradeInLabel: null, tradeInVin: null, tradeInValuationId: null, tradeInValue: null, containers: [], version: 4,
    leasingDownPayment: null, leasingTermMonths: null, leasingKmPerYear: null,
    liveContractId: null, liveContractNumber: null,
    ...overrides,
  }) as unknown as SalesOfferRead

const CONTRACTED = offer({ liveContractId: 'k7', liveContractNumber: 'V-000007' })

function install(read: SalesOfferRead, tradeIn: () => unknown = () => read, lineItems: unknown[] = []) {
  return installFakeBackend([
    { method: 'GET', match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
    { method: 'GET', match: /^\/customers\/cust-1\/(phones|emails)$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/customers\/cust-1\/(vehicles|external-ids|audit-log)$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => read },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: lineItems }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    { method: 'POST', match: /^\/sales\/offers\/o1\/trade-in$/, handler: tradeIn },
  ])
}

function ContractStub() {
  return <div data-testid="contract-page">{useLocation().pathname}</div>
}

function renderWorkspace() {
  return renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      <Route path="/sales/contracts/:id" element={<ContractStub />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
}

const refusalText = (contractNumber: string) => i18n.t('offerWorkspace.errors.saveRefused.offerHasContract', { contractNumber })

beforeEach(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('OfferWorkspace — an offer with a live contract is read-only (KAN-426)', () => {
  it('names the contract, disables every container and Continue, and opens the contract', async () => {
    const user = userEvent.setup()
    const backend = install(CONTRACTED)
    renderWorkspace()

    expect(await screen.findByText(refusalText('V-000007'))).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.leasing.termMonths') })).toBeDisabled()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') })).toBeDisabled()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.customer.search') })).toBeDisabled()
    const continueButton = screen.getByRole('button', { name: i18n.t('offerWorkspace.continue') })
    expect(continueButton).toBeDisabled()
    await user.hover(continueButton.parentElement as HTMLElement)
    expect(await screen.findAllByText(refusalText('V-000007'))).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.liveContract.openContract') }))
    expect(await screen.findByTestId('contract-page')).toHaveTextContent('/sales/contracts/k7')
    expect(backend.callsTo(/^\/sales\/offers\/o1/, 'PATCH')).toHaveLength(0)
  })

  // A disabled fieldset does not stop a NumberInput's steppers (they act on
  // pointer-down): read-only, the number inputs show none.
  it('the number inputs show no steppers', async () => {
    install(offer({ liveContractId: 'k7', liveContractNumber: 'V-000007', vehicleSource: 'manual', vehicleSnapshotFrozenAt: null, discountType: 'percent' }))
    renderWorkspace()

    for (const key of [
      'offerWorkspace.leasing.downPayment',
      'offerWorkspace.leasing.termMonths',
      'offerWorkspace.leasing.kmPerYear',
      'offerWorkspace.pricing.manualBasePrice',
      'offerWorkspace.pricing.discountValue',
    ]) {
      const input = await screen.findByRole('textbox', { name: i18n.t(key) })
      expect(input).toBeDisabled()
      const wrapper = input.closest('.mantine-NumberInput-root') as HTMLElement
      expect(wrapper.querySelectorAll('button')).toHaveLength(0)
    }
  })

  it('an offer without a live contract stays editable and names none', async () => {
    install(offer())
    renderWorkspace()

    expect(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') })).toBeEnabled()
    expect(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.leasing.termMonths') })).toBeEnabled()
    expect(screen.queryByRole('button', { name: i18n.t('offerWorkspace.liveContract.openContract') })).toBeNull()
  })

  it('a trade-in save refused for a contract made meanwhile says the same sentence', async () => {
    const user = userEvent.setup()
    install(offer(), () =>
      status(409, {
        error: {
          code: 'conflict',
          message: 'Offer ANG-2026-0001 has contract V-000008 — edit the contract.',
          details: { reason: 'offer_has_contract', contractId: 'k8', contractNumber: 'V-000008' },
        },
      }),
    )
    renderWorkspace()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
    await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel')), 'WVWZZZ1KZAW331338')
    await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.labelLabel')), 'VW Golf')
    await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(screen.getByText(refusalText('V-000008'))).toBeInTheDocument())
    expect(screen.queryByText(i18n.t('offerWorkspace.tradeIn.saveError'))).toBeNull()
    expect(screen.queryByText(/edit the contract/)).toBeNull()
  })

  // KAN-501 — read-only must not take the view-only paths with it: the
  // selected customer and «zum Lagerfahrzeug» still open their overlays
  // (ADR-059), while the controls that write stay disabled.
  it('the selected customer still opens as an overlay while the workspace stays read-only', async () => {
    const user = userEvent.setup()
    const backend = install(offer({ liveContractId: 'k7', liveContractNumber: 'V-000007', customerId: 'cust-1', customerLabel: 'Hans Muster' }))
    renderWorkspace()

    const customerButton = await screen.findByRole('button', { name: 'Hans Muster' })
    expect(customerButton).toBeEnabled()
    await user.click(customerButton)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.leasing.termMonths') })).toBeDisabled()
    expect(backend.callsTo(/^\/sales\/offers\/o1/, 'PATCH')).toHaveLength(0)
  })

  it('«zum Lagerfahrzeug» still opens the car, and the pricing inputs stay disabled', async () => {
    const user = userEvent.setup()
    const backend = install(
      offer({
        liveContractId: 'k7', liveContractNumber: 'V-000007', vehicleSource: 'stock', stockItemId: 's1', vehicleLabel: 'VW Golf',
        discountType: 'percent', containers: [{ id: 'pricing', requirement: 'required', status: 'in_progress' }],
      } as Partial<SalesOfferRead>),
    )
    renderWorkspace()

    const stockLink = await screen.findByRole('button', { name: i18n.t('offerWorkspace.pricing.noPriceLink') })
    expect(stockLink).toBeEnabled()
    // Every field on the workspace, pricing's own included, stays read-only.
    for (const field of screen.getAllByRole('textbox')) expect(field).toBeDisabled()
    await user.click(stockLink)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/^\/inventory\/stock-items\/s1/, 'GET').length).toBeGreaterThan(0))
    expect(backend.callsTo(/^\/sales\/offers\/o1/, 'PATCH')).toHaveLength(0)
  })

  // KAN-501 — the line items moved under the pricing container's own
  // fieldset: with a frozen snapshot they render, and none of them writes.
  it('the factory options and accessories stay read-only, and nothing is saved', async () => {
    const user = userEvent.setup()
    const option = {
      id: 'li-1', kind: 'factory_option', code: 'LED', label: 'LED-Scheinwerfer', unitPrice: '800.00', quantity: 1,
      included: true, discountType: null, discountValue: null, discountResolvedAmount: null, discountSuppressedReason: null, position: 0,
    }
    const accessory = { ...option, id: 'li-2', kind: 'accessory', code: 'MAT', label: 'Fussmatten', unitPrice: '120.00', position: 1 }
    const backend = install(
      offer({
        liveContractId: 'k7', liveContractNumber: 'V-000007', vehicleSource: 'stock', stockItemId: 's1', vehicleLabel: 'VW Golf',
        vehicleSnapshotFrozenAt: '2026-02-01T00:00:00Z', discountType: 'percent',
      } as Partial<SalesOfferRead>),
      undefined,
      [option, accessory],
    )
    renderWorkspace()

    const checkbox = await screen.findByRole('checkbox', { name: 'LED-Scheinwerfer' })
    expect(checkbox).toBeDisabled()
    expect(screen.getByRole('button', { name: i18n.t('common.remove') })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.lineItems.accessoryLabel') })).toBeDisabled()
    await user.click(checkbox)
    expect(backend.callsTo(/^\/sales\/offers\/o1/, 'PUT')).toHaveLength(0)
    expect(backend.callsTo(/^\/sales\/offers\/o1/, 'PATCH')).toHaveLength(0)
  })

  it('an offer with a live contract and no customer offers neither search nor create', async () => {
    install(offer({ liveContractId: 'k7', liveContractNumber: 'V-000007' }))
    renderWorkspace()

    expect(await screen.findByRole('button', { name: i18n.t('offerWorkspace.customer.search') })).toBeDisabled()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.customer.createNew') })).toBeDisabled()
  })
})
