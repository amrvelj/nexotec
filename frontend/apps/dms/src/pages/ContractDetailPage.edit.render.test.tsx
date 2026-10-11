// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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

  it('a discount typed just before Confirm is saved first, then signed with its version (review finding)', async () => {
    let current: SalesContractRead = { ...PRICED, discountType: 'amount' }
    const backend = installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: (req) => {
          current = { ...current, ...(req.body as object), grossPrice: '17000.00', version: current.version + 1 }
          return current
        },
      },
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/confirm$/,
        handler: () => {
          current = { ...current, status: 'confirmed', version: current.version + 1 }
          return current
        },
      },
      ...baseRoutes(() => current),
    ])
    renderPage()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '1000' } })
    const confirmButton = screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })
    // Within the debounce the typed figure is unsaved: Confirm waits for it.
    expect(confirmButton).toBeDisabled()
    fireEvent.click(confirmButton)
    expect(backend.callsTo(/\/confirm$/, 'POST')).toHaveLength(0)

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    await waitFor(() => expect(backend.callsTo(/\/confirm$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')[0].body).toMatchObject({ discountValue: 1000 })
    expect(backend.callsTo(/\/confirm$/, 'POST')[0].headers.get('If-Match')).toBe(String(PRICED.version + 1))
  })

  it('a line-item save still on its way holds Confirm until it lands (KAN-288 review)', async () => {
    const user = userEvent.setup()
    let release: (value: unknown) => void = () => {}
    const OPTION = {
      id: 'li-1', kind: 'factory_option', code: 'LED', label: 'LED-Scheinwerfer', unitPrice: '800.00', quantity: 1,
      included: true, discountType: null, discountValue: null, discountResolvedAmount: null,
      discountSuppressedReason: null, position: 0,
    }
    const backend = installFakeBackend([
      {
        method: 'PUT',
        match: /^\/sales\/contracts\/k1\/line-items$/,
        handler: () => new Promise((resolve) => (release = resolve)),
      },
      { match: /^\/sales\/contracts\/k1\/line-items$/, handler: () => ({ items: [OPTION] }) },
      ...baseRoutes(() => PRICED),
    ])
    renderPage()

    await user.click(await screen.findByRole('checkbox', { name: 'LED-Scheinwerfer' }))
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'PUT')).toHaveLength(1))
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()

    release({ ...PRICED, optionsTotal: '0.00', version: PRICED.version + 1 })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled())
  })

  it('a stale-version line-item refusal is said in the user\'s language and reloads the lines (KAN-288 review)', async () => {
    const user = userEvent.setup()
    const OPTION = {
      id: 'li-1', kind: 'factory_option', code: 'LED', label: 'LED-Scheinwerfer', unitPrice: '800.00', quantity: 1,
      included: true, discountType: null, discountValue: null, discountResolvedAmount: null,
      discountSuppressedReason: null, position: 0,
    }
    const backend = installFakeBackend([
      {
        method: 'PUT',
        match: /^\/sales\/contracts\/k1\/line-items$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'SalesContract has been modified since If-Match version 2 (current version is 3).',
              details: { currentVersion: 3, ifMatchVersion: 2 },
            },
          }),
      },
      { match: /^\/sales\/contracts\/k1\/line-items$/, handler: () => ({ items: [OPTION] }) },
      ...baseRoutes(() => PRICED),
    ])
    renderPage()

    await user.click(await screen.findByRole('checkbox', { name: 'LED-Scheinwerfer' }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.editRefused.staleVersion'))).toBeInTheDocument()
    expect(screen.queryByText(/has been modified since/)).not.toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/line-items$/, 'GET').length).toBeGreaterThanOrEqual(2))
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
