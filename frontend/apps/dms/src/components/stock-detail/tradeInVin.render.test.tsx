// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { ApiError } from '../../api/client'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, PICKED_CODED_VALUES, configurationRead } from '../../test/configuratorFixtures'
import type { ConfigurationRead, StockItemRead } from '../../api/types'
import { DetailsTab } from './DetailsTab'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'

// KAN-536 (Anto, 2026-11-12, option A) — a trade-in's pipeline item keeps the
// VIN its contract holds (`tradeInVin`). «Einkauf buchen» prefills it, and a
// different VIN is refused by the server (422 `trade_in_vin_differs`) and
// said in the UI language, the way a configuration's VIN is checked
// (KAN-510). A configuration that carries a VIN decides, as on the server.
// The signed contract is frozen, so this refusal offers no edit.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const CONTRACT_VIN = 'WVWZZZ1KZAW536001'
const OTHER_VIN = 'WVWZZZ1KZAW536002'
const CONFIGURED_VIN = 'WVWZZZ1KZAW536003'
const NOW = '2026-11-12T09:00:00Z'

const TRADE_IN_ITEM: StockItemRead = {
  id: 's1', stockNumber: 'S-000536', vehicleLabel: 'Seat Leon', vin: null, vehicleId: null,
  condition: 'used', lifecycleStatus: 'pipeline', reservationState: 'none', ageingBucket: 'green',
  odometerKm: null, listPrice: null, effectivePrice: null, basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: null, inStockAt: null, leftStockAt: null, isInvoiceable: false,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
  pipelineRef: 'contract:k-1:trade_in', purchaseDate: null, purchaseInvoiceRef: null, purchasePrice: null,
  supplierIsVatRegistered: null, supplierName: null, valuationRefAmount: null, valuationRefId: null,
  valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 1,
  configurationId: null, tradeInVin: CONTRACT_VIN,
}

const RECORDED: Partial<ConfigurationRead> = {
  source: 'manual', mode: 'record', catalogueVariantId: null, catalogueVariantLabel: null, ...PICKED_CODED_VALUES,
}

const vinField = () =>
  screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.fields.vin')) }) as HTMLInputElement
const field = (key: string) => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t(key)) })
const submit = () => screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') })

function configurationBackend(configuration: ConfigurationRead) {
  return installFakeBackend([
    ...CODED_REFERENCE_ROUTES,
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/configurations\/cfg-1$/, handler: () => configuration },
  ])
}

describe('RecordPurchaseDialog — the VIN a trade-in\'s contract holds (KAN-536)', () => {
  it('prefills it, reading no configuration, and sends it', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([])
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin tradeInVin={CONTRACT_VIN} onClose={() => {}} onSubmit={onSubmit} />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONTRACT_VIN))
    expect(backend.callsTo(/^\/configurations\//, 'GET')).toHaveLength(0)

    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(submit())
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ vin: CONTRACT_VIN })
  })

  it('leaves the field empty for an item that keeps none (created before KAN-536)', () => {
    installFakeBackend([])
    renderWithProviders(<RecordPurchaseDialog opened requiresVin tradeInVin={null} onClose={() => {}} onSubmit={async () => {}} />)

    expect(vinField().value).toBe('')
  })

  it('prefills it when the configuration carries no VIN', async () => {
    const backend = configurationBackend(configurationRead({ ...RECORDED, vin: null }))
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin configurationId="cfg-1" tradeInVin={CONTRACT_VIN} onClose={() => {}} onSubmit={async () => {}} />,
    )

    await waitFor(() => expect(backend.callsTo(/^\/configurations\/cfg-1$/, 'GET')).toHaveLength(1))
    await waitFor(() => expect(vinField().value).toBe(CONTRACT_VIN))
  })

  it('lets the configuration\'s VIN decide when it carries one', async () => {
    configurationBackend(configurationRead({ ...RECORDED, vin: CONFIGURED_VIN }))
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin configurationId="cfg-1" tradeInVin={CONTRACT_VIN} onClose={() => {}} onSubmit={async () => {}} />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
  })

  it('says a differing VIN in the UI language, keeps the dialog open and offers no edit', async () => {
    const user = userEvent.setup()
    installFakeBackend([])
    const refused = new ApiError(422, 'unprocessable_entity', "The VIN is not the one the trade-in's contract holds.", {
      field: 'vin',
      reason: 'trade_in_vin_differs',
    })
    const onClose = vi.fn()
    renderWithProviders(
      <RecordPurchaseDialog
        opened
        requiresVin
        tradeInVin={CONTRACT_VIN}
        onClose={onClose}
        onEditConfiguration={vi.fn()}
        onSubmit={() => Promise.reject(refused)}
      />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONTRACT_VIN))
    await user.clear(vinField())
    await user.type(vinField(), OTHER_VIN)
    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(submit())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Diese VIN ist nicht die VIN, die im Vertrag für das Eintauschfahrzeug erfasst ist.')
    expect(alert).toHaveTextContent(i18n.t('apiRefusal.reasons.trade_in_vin_differs'))
    expect(screen.queryByText(/trade-in's contract holds/)).not.toBeInTheDocument()
    expect(within(alert).queryByRole('button')).toBeNull()
    expect(onClose).not.toHaveBeenCalled()
    expect(vinField().value).toBe(OTHER_VIN) // what was typed stays, for the correction
  })

  it.each(['fr', 'it', 'en'])('has the refusal in %s', async (language) => {
    await i18n.changeLanguage(language)
    installFakeBackend([])
    const user = userEvent.setup()
    const refused = new ApiError(422, 'unprocessable_entity', 'refused', { field: 'vin', reason: 'trade_in_vin_differs' })
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin tradeInVin={CONTRACT_VIN} onClose={() => {}} onSubmit={() => Promise.reject(refused)} />,
    )

    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(submit())

    const text = i18n.t('apiRefusal.reasons.trade_in_vin_differs')
    expect(text).not.toBe(i18n.getFixedT('de')('apiRefusal.reasons.trade_in_vin_differs'))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })
})

describe('Stock detail — «Einkauf erfassen» on a trade-in\'s pipeline item (KAN-536)', () => {
  const renderDetails = (item: StockItemRead) =>
    renderWithProviders(
      <DetailsTab
        item={item}
        locale="de-CH"
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        canRecordPurchase
        requestedAction="recordPurchase"
        onRecordPurchase={vi.fn()}
      />,
    )

  it('opens the dialog with the contract\'s VIN', async () => {
    installFakeBackend([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }])
    renderDetails(TRADE_IN_ITEM)

    await waitFor(() => expect(vinField().value).toBe(CONTRACT_VIN))
  })

  it('asks for no VIN once the car carries its own (a stornoed purchase)', async () => {
    installFakeBackend([{ method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) }])
    renderDetails({ ...TRADE_IN_ITEM, vin: OTHER_VIN, vehicleId: 'v-1' })

    await screen.findByRole('button', { name: i18n.t('stockDetail.purchase.submit') })
    expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.fields.vin')) })).toBeNull()
  })
})
