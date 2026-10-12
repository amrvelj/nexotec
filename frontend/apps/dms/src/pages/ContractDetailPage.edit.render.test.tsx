// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, configurationRead, pickCodedFields } from '../test/configuratorFixtures'
import type { SalesContractRead } from '../api/types'
import { ContractDetailPage } from './ContractDetailPage'

// KAN-67 (ADR-083) — a contract born from a customer (KAN-58) used to be a
// dead end: no vehicle, no price, nothing to add them with. While pending it
// now carries the offer workspace's own vehicle, price and trade-in
// containers, saving through PATCH /sales/contracts/{id}; confirmed, it is
// frozen and they are gone.

// The configurator's grid virtualises its rows; jsdom has no layout, so it
// renders every row instead (as OfferWorkspace.configurator.render.test.tsx).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

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

  // KAN-315 (Anto, 2026-10-12) — such a contract without a buyer could be
  // neither given one (the edit section was hidden) nor confirmed
  // (KAN-307). It now offers the customer container alone: the buyer, never
  // the vehicle, price or trade-in.
  const PRICED_WITHOUT_INPUTS_NO_BUYER: SalesContractRead = {
    ...PRICED,
    vehicleSnapshotFrozenAt: null,
    manualBasePrice: null,
    customerId: null,
    customerLabel: null,
    containers: PRICED.containers?.map((c) => (c.id === 'customer' ? { ...c, status: 'not_started' } : c)),
  }

  it('a contract priced before it kept its inputs and without a buyer offers the buyer picker, and nothing else', async () => {
    installFakeBackend(baseRoutes(() => PRICED_WITHOUT_INPUTS_NO_BUYER))
    renderPage()

    const section = await screen.findByTestId('contract-priced-without-inputs')
    expect(within(section).getByText(i18n.t('contractDetail.edit.pricedWithoutInputsNoBuyer'))).toBeInTheDocument()
    expect(within(section).getByText(i18n.t('offerWorkspace.containers.customer'))).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: i18n.t('offerWorkspace.customer.search') })).toBeInTheDocument()
    expect(within(section).getByText(i18n.t('offerWorkspace.customer.emptyHintContract'))).toBeInTheDocument()
    expect(screen.queryByTestId('contract-edit')).not.toBeInTheDocument()
    // No vehicle, price or trade-in editor anywhere (the hero stat row says
    // "Eintauschfahrzeug" too, so the editors are asserted, not the titles).
    for (const title of ['vehicle', 'pricing', 'tradeIn'] as const) {
      expect(within(section).queryByText(i18n.t(`offerWorkspace.containers.${title}`))).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: i18n.t('offerWorkspace.vehicle.search') })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountType') })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()
  })

  it('picking the buyer of such a contract sends the customer only, and Confirm turns on', async () => {
    const user = userEvent.setup()
    let current = PRICED_WITHOUT_INPUTS_NO_BUYER
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /^\/customers$/,
        handler: () => ({
          items: [{ id: 'cust-9', customerNumber: 'K-000009', firstName: 'Ursula', lastName: 'Vogt', companyName: null }],
          nextCursor: null,
        }),
      },
      { method: 'GET', match: /^\/customers\/cust-9$/, handler: () => ({ id: 'cust-9', lifecycleStatus: 'active' }) },
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: () => {
          current = { ...current, customerId: 'cust-9', customerLabel: 'Ursula Vogt', version: current.version + 1 }
          return current
        },
      },
      ...baseRoutes(() => current),
    ])
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.customer.search') }))
    await user.type(await screen.findByPlaceholderText(i18n.t('offerWorkspace.customer.search')), 'Vogt')
    await user.click(await screen.findByText('Ursula Vogt'))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')
    expect(patch.body).toEqual({ customerId: 'cust-9' })
    expect(patch.headers.get('If-Match')).toBe(String(PRICED_WITHOUT_INPUTS_NO_BUYER.version))
    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
    const section = screen.getByTestId('contract-priced-without-inputs')
    expect(within(section).getByText('Ursula Vogt')).toBeInTheDocument()
    expect(within(section).getByText(i18n.t('contractDetail.edit.pricedWithoutInputs'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('offerWorkspace.customer.search') })).not.toBeInTheDocument()
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

  it('a stale-version line-item refusal is said in the user\'s language and reloads the lines and the contract (KAN-288 review)', async () => {
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
    // KAN-289 — and the contract itself, so the next save carries the current version.
    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'GET').length).toBeGreaterThanOrEqual(2))
  })

  it('a price save still on its way holds Confirm until it lands (KAN-289)', async () => {
    let release: (value: unknown) => void = () => {}
    const backend = installFakeBackend([
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: () => new Promise((resolve) => (release = resolve)),
      },
      ...baseRoutes(() => ({ ...PRICED, discountType: 'amount' })),
    ])
    renderPage()

    const discount = await screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
    fireEvent.change(discount, { target: { value: '1000' } })
    // Past the debounce the figure is sent; while the PATCH is unanswered Confirm still waits.
    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1), { timeout: 2000 })
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()

    release({ ...PRICED, discountType: 'amount', discountValue: '1000.00', grossPrice: '17000.00', version: PRICED.version + 1 })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled())
  })

  it('a trade-in save still on its way holds Confirm until it lands (KAN-289)', async () => {
    const user = userEvent.setup()
    let release: (value: unknown) => void = () => {}
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/trade-in$/,
        handler: () => new Promise((resolve) => (release = resolve)),
      },
      ...baseRoutes(() => PRICED),
    ])
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
    await user.type(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.tradeIn.vinLabel') }), 'WVWZZZ1KZAW555555')
    await user.type(screen.getByRole('textbox', { name: i18n.t('offerWorkspace.tradeIn.labelLabel') }), 'Opel Corsa')
    await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(backend.callsTo(/\/trade-in$/, 'POST')).toHaveLength(1))
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()

    release({ ...PRICED, tradeInVehicleId: 'veh-9', tradeInLabel: 'Opel Corsa', version: PRICED.version + 1 })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled())
  })

  // picks the trade-in's five coded fields first (Anto, 2026-10-12)
  it('a trade-in valuation attach still on its way holds Confirm until it lands (KAN-289 review)', { timeout: 15_000 }, async () => {
    const user = userEvent.setup()
    let release: (value: unknown) => void = () => {}
    const tradeIn = configurationRead({
      id: 'cfg-ti', mode: 'record', source: 'manual', catalogueVariantId: null,
      brandDisplayName: 'Subaru', modelGroupName: 'Justy', variantName: 'G3X', licencePlate: 'ZH123456', mileageKm: 148000,
    })
    const catalogue: FakeRoute[] = [
      { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
      { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
      {
        method: 'GET',
        match: /\/catalogue\/variants$/,
        handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
      },
      ...CODED_REFERENCE_ROUTES,
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    ]
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: tradeIn }) },
      { method: 'GET', match: /^\/configurations\/cfg-ti$/, handler: () => tradeIn },
      { method: 'POST', match: /^\/valuations$/, handler: () => ({ __status: 201, body: { id: 'val-7' } }) },
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/trade-in\/valuation$/,
        handler: () => new Promise((resolve) => (release = resolve)),
      },
      { method: 'GET', match: /^\/valuations\/val-7$/, handler: () => ({ id: 'val-7', source: 'manual' }) },
      ...catalogue,
      ...baseRoutes(() => PRICED),
    ])
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.valueWithConfigurator') }))
    const overlay = await screen.findByRole('dialog')
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await pickCodedFields()
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))
    const valuationDialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    await user.type(within(valuationDialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false }), '2500')
    await user.click(within(valuationDialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))

    await waitFor(() => expect(backend.callsTo(/\/trade-in\/valuation$/, 'POST')).toHaveLength(1))
    expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()

    release({
      ...PRICED, tradeInValuationId: 'val-7', tradeInConfigurationId: 'cfg-ti', tradeInLabel: 'Subaru Justy G3X',
      tradeInValue: '2500.00', payable: '15500.00', version: PRICED.version + 1,
    })
    await waitFor(() => expect(screen.getByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled())
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
