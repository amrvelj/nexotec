// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesContractRead } from '../api/types'
import { ContractDetailPage } from './ContractDetailPage'

// KAN-67 (ADR-083) — a contract born from a customer (KAN-58) used to be a
// dead end: no vehicle, no price, nothing to add them with. While pending it
// now carries the offer workspace's own vehicle, price and trade-in
// containers, saving through PATCH /sales/contracts/{id}; confirmed, it is
// frozen and they are gone.

const EMPTY: SalesContractRead = {
  id: 'k1',
  contractNumber: 'C-000007',
  offerId: null,
  offerNumber: null,
  status: 'pending',
  customerId: 'cust-1',
  customerLabel: 'Ursula Vogt',
  customerLocality: null,
  vehicleSource: null,
  stockItemId: null,
  vehicleLabel: null,
  configurationId: null,
  configurationLabel: null,
  manualVehicleCondition: null,
  manualBasePrice: null,
  vehicleSnapshotFrozenAt: null,
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
  tradeInVehicleId: null,
  tradeInLabel: null,
  tradeInVin: null,
  tradeInConfigurationId: null,
  tradeInValuationId: null,
  tradeInValue: null,
  tradeInPurchasePrice: null,
  payable: null,
  financing: 'cash',
  reservationId: null,
  signedAt: null,
  deliveryDate: null,
  isInvoiceable: false,
  isLegacy: false,
  invoiceRef: null,
  cancelledReason: null,
  containers: [
    { id: 'customer', requirement: 'required', status: 'complete' },
    { id: 'vehicle', requirement: 'required', status: 'not_started' },
    { id: 'pricing', requirement: 'required', status: 'not_started' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: null,
  version: 1,
  createdAt: '2026-10-10T08:00:00Z',
  updatedAt: '2026-10-10T08:00:00Z',
}

// What the server answers once a hand-entered car with a price is on it.
const PRICED: SalesContractRead = {
  ...EMPTY,
  vehicleSource: 'manual',
  vehicleLabel: 'Volkswagen Käfer',
  manualVehicleCondition: 'used',
  manualBasePrice: '18000.00',
  vehicleSnapshotFrozenAt: '2026-10-10T08:05:00Z',
  basePrice: '18000.00',
  optionsTotal: '0.00',
  listPrice: '18000.00',
  accessoriesTotal: '0.00',
  totalBeforeDiscount: '18000.00',
  discountAmount: '0.00',
  grossPrice: '18000.00',
  payable: '18000.00',
  vehicleCondition: 'used',
  containers: [
    { id: 'customer', requirement: 'required', status: 'complete' },
    { id: 'vehicle', requirement: 'required', status: 'complete' },
    { id: 'pricing', requirement: 'required', status: 'complete' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
  ],
  version: 2,
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
    </Routes>,
    { route: '/sales/contracts/k1' },
  )
}

const baseRoutes = (contract: () => SalesContractRead) => [
  { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => contract() },
  { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
  { match: /^\/sales\/contracts\/k1\/line-items$/, handler: () => ({ items: [] }) },
]

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ContractDetailPage — a pending contract can be completed (KAN-67)', () => {
  it('shows the vehicle, price build-up and trade-in containers while pending', async () => {
    installFakeBackend(baseRoutes(() => EMPTY))
    renderPage()

    const edit = await screen.findByTestId('contract-edit')
    expect(within(edit).getByText(i18n.t('contractDetail.edit.title'))).toBeInTheDocument()
    expect(within(edit).getByText(i18n.t('offerWorkspace.containers.vehicle'))).toBeInTheDocument()
    expect(within(edit).getByText(i18n.t('offerWorkspace.containers.pricing'))).toBeInTheDocument()
    expect(within(edit).getByText(i18n.t('offerWorkspace.containers.tradeIn'))).toBeInTheDocument()
    expect(within(edit).getByRole('button', { name: i18n.t('offerWorkspace.vehicle.search') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()
  })

  it('a discount is saved on the contract with its If-Match, and the confirm action turns on with a price', async () => {
    const user = userEvent.setup()
    let current = PRICED
    const backend = installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: () => {
          current = { ...PRICED, discountType: 'amount', version: PRICED.version + 1 }
          return current
        },
      },
      ...baseRoutes(() => current),
    ])
    renderPage()

    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
    await user.click(await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountType') }))
    await user.click(await screen.findByRole('option', { name: i18n.t('offerWorkspace.pricing.discountAmount') }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')
    expect(call.headers.get('If-Match')).toBe(String(PRICED.version))
    expect(call.body).toMatchObject({ discountType: 'amount' })
    expect(backend.callsTo(/^\/sales\/offers/)).toHaveLength(0)
  })

  it('an edit refused because the contract was confirmed elsewhere says so, localised', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: "Contract C-000007 can no longer be edited (status 'confirmed').",
              details: { reason: 'contract_not_pending', status: 'confirmed' },
            },
          }),
      },
      ...baseRoutes(() => PRICED),
    ])
    renderPage()

    await user.click(await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountType') }))
    await user.click(await screen.findByRole('option', { name: i18n.t('offerWorkspace.pricing.discountAmount') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.editRefused.notPending'))).toBeInTheDocument()
    expect(screen.queryByText(/can no longer be edited/)).not.toBeInTheDocument()
  })

  it('a confirmed contract is frozen: no containers to edit', async () => {
    installFakeBackend(baseRoutes(() => ({ ...PRICED, status: 'confirmed', signedAt: '2026-10-10T09:00:00Z' })))
    renderPage()

    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()
    expect(screen.queryByTestId('contract-edit')).not.toBeInTheDocument()
  })

  it('a car with no price entered (CHF 0.00, pricing in progress) can still be confirmed — Anto, 2026-10-10', async () => {
    installFakeBackend(
      baseRoutes(() => ({
        ...PRICED,
        manualBasePrice: null,
        basePrice: '0.00',
        listPrice: '0.00',
        grossPrice: '0.00',
        payable: '0.00',
        containers: PRICED.containers?.map((c) => (c.id === 'pricing' ? { ...c, status: 'in_progress' } : c)),
      })),
    )
    renderPage()

    expect(await screen.findByTestId('contract-edit')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('offerWorkspace.pricing.noPriceTitle'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
  })

  it('a contract priced before it kept its inputs shows why it cannot be edited, and can still be confirmed', async () => {
    installFakeBackend(baseRoutes(() => ({ ...PRICED, vehicleSnapshotFrozenAt: null, manualBasePrice: null })))
    renderPage()

    expect(await screen.findByText(i18n.t('contractDetail.edit.pricedWithoutInputs'))).toBeInTheDocument()
    expect(screen.queryByTestId('contract-edit')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
  })

  it('renders the edit section in Italian', async () => {
    await i18n.changeLanguage('it')
    installFakeBackend(baseRoutes(() => EMPTY))
    renderPage()

    const title = i18n.t('contractDetail.edit.title')
    expect(title).not.toBe('Complete the contract')
    expect(await screen.findByText(title)).toBeInTheDocument()
  })
})
