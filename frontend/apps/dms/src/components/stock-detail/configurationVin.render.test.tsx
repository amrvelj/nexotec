// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { ApiError } from '../../api/client'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, PICKED_CODED_VALUES, configurationRead } from '../../test/configuratorFixtures'
import type { ConfigurationRead, StockItemRead } from '../../api/types'
import { DetailsTab } from './DetailsTab'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'

// KAN-510 (Anto, 2026-11-01: option B, then A) — a pipeline car is bought
// with the VIN of its configuration. The purchase dialog prefills it; the
// server refuses another (422 `configuration_vin_differs`), and the refusal
// says so in the UI language with «Konfiguration bearbeiten», which opens the
// configuration in the configurator — in its own mode — where a wrong VIN is
// corrected. The stock detail offers the same entry on the configuration
// card, to a role that may write configurations. The configuration's PATCH
// is the only write: Stock writes nothing of it.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const CONFIGURED_VIN = 'WVWZZZ1KZAW005101'
const CORRECTED_VIN = 'WVWZZZ1KZAW005102'
const NOW = '2026-11-01T09:00:00Z'

const PIPELINE_ITEM: StockItemRead = {
  id: 's1', stockNumber: 'S-000510', vehicleLabel: 'Škoda Octavia Combi', vin: null, vehicleId: null,
  condition: 'used', lifecycleStatus: 'pipeline', reservationState: 'none', ageingBucket: 'green',
  odometerKm: null, listPrice: null, effectivePrice: null, basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: null, inStockAt: null, leftStockAt: null, isInvoiceable: false,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
  pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null, purchasePrice: null, supplierIsVatRegistered: null,
  supplierName: null, valuationRefAmount: null, valuationRefId: null, valuationRefSource: null,
  valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 1,
  configurationId: 'cfg-1',
}

/** A car recorded with its VIN — a manual `record` configuration. */
const RECORDED: Partial<ConfigurationRead> = {
  source: 'manual',
  mode: 'record',
  catalogueVariantId: null,
  catalogueVariantLabel: null,
  ...PICKED_CODED_VALUES,
  vin: CONFIGURED_VIN,
}

const vinField = () =>
  screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.fields.vin')) }) as HTMLInputElement
const field = (key: string) => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t(key)) })
const editLabel = () => i18n.t('stockDetail.configuration.edit')

function configurationBackend(configuration: ConfigurationRead, extra: FakeRoute[] = []) {
  const patches: Record<string, unknown>[] = []
  let current = configuration
  const backend = installFakeBackend([
    ...extra,
    ...CODED_REFERENCE_ROUTES,
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/configurations\/cfg-1$/, handler: () => current },
    {
      method: 'PATCH',
      match: /^\/configurations\/cfg-1$/,
      handler: (req) => {
        const body = req.body as Record<string, unknown>
        patches.push(body)
        current = { ...current, vin: (body.vin as string | null) ?? null, version: current.version + 1 }
        return current
      },
    },
    {
      method: 'PATCH',
      match: /^\/configurations\/cfg-1\/options$/,
      handler: () => {
        current = { ...current, version: current.version + 1 }
        return current
      },
    },
  ])
  return { backend, patches }
}

describe('RecordPurchaseDialog — the VIN comes from the configuration (KAN-510)', () => {
  it('prefills the configuration\'s VIN', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin configurationId="cfg-1" onClose={() => {}} onSubmit={async () => {}} />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
  })

  it('leaves the VIN to the user when the configuration has none (a factory order)', async () => {
    const { backend } = configurationBackend(configurationRead({ ...RECORDED, mode: 'build', vin: null }))
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin configurationId="cfg-1" onClose={() => {}} onSubmit={async () => {}} />,
    )

    await waitFor(() => expect(backend.callsTo(/^\/configurations\/cfg-1$/, 'GET')).toHaveLength(1))
    expect(vinField().value).toBe('')
  })

  it('says a refused VIN in the UI language and offers «Konfiguration bearbeiten» on the configuration', async () => {
    const user = userEvent.setup()
    await i18n.changeLanguage('fr')
    configurationBackend(configurationRead(RECORDED))
    const refused = new ApiError(422, 'unprocessable_entity', "The configuration's VIN is not this stock item's VIN.", {
      field: 'vin',
      reason: 'configuration_vin_differs',
    })
    const onEditConfiguration = vi.fn()
    renderWithProviders(
      <RecordPurchaseDialog
        opened
        requiresVin
        configurationId="cfg-1"
        onClose={() => {}}
        onEditConfiguration={onEditConfiguration}
        onSubmit={() => Promise.reject(refused)}
      />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
    await user.clear(vinField())
    await user.type(vinField(), CORRECTED_VIN)
    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('stockDetail.purchase.configurationVinDiffers'))
    expect(alert).toHaveTextContent('Le VIN diffère de celui de la configuration.')
    expect(screen.queryByText(/not this stock item/)).not.toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: editLabel() }))
    expect(onEditConfiguration).toHaveBeenCalledTimes(1)
    expect(onEditConfiguration.mock.calls[0][0]).toMatchObject({ id: 'cfg-1', vin: CONFIGURED_VIN })
  })

  it('offers no button to a role that may not write configurations', async () => {
    const user = userEvent.setup()
    configurationBackend(configurationRead(RECORDED))
    const refused = new ApiError(422, 'unprocessable_entity', 'refused', { field: 'vin', reason: 'configuration_vin_differs' })
    renderWithProviders(
      <RecordPurchaseDialog opened requiresVin configurationId="cfg-1" onClose={() => {}} onSubmit={() => Promise.reject(refused)} />,
    )

    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('stockDetail.purchase.configurationVinDiffers'))
    expect(within(alert).queryByRole('button')).toBeNull()
  })
})

describe('Stock detail — «Konfiguration bearbeiten» (KAN-510)', () => {
  const renderDetails = (canEditConfiguration: boolean, onRecordPurchase = vi.fn()) =>
    renderWithProviders(
      <DetailsTab
        item={PIPELINE_ITEM}
        locale="de-CH"
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        canEditConfiguration={canEditConfiguration}
        onRecordPurchase={onRecordPurchase}
      />,
    )

  it('is on the configuration card for a role that may write configurations', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderDetails(true)

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).getByRole('button', { name: editLabel() })).toBeInTheDocument()
  })

  it('is not shown to a role that may not', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderDetails(false)

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).queryByRole('button', { name: editLabel() })).toBeNull()
    expect(within(card).queryByRole('button', { name: i18n.t('configurator.summary.open') })).toBeNull()
  })

  // KAN-510 (Anto, 2026-11-02, option A): every stock car, in the pipeline
  // or in stock, unless a contract reserves it — a contract reserves a car
  // only when it is signed, so the card then offers «Vertrag bearbeiten»
  // disabled, with the reason.
  const renderItem = (item: StockItemRead, { canEditConfiguration = true, onRecordPurchase = vi.fn() } = {}) =>
    renderWithProviders(
      <DetailsTab
        item={item}
        locale="de-CH"
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        canEditConfiguration={canEditConfiguration}
        onRecordPurchase={onRecordPurchase}
      />,
    )
  const RESERVED: StockItemRead = { ...PIPELINE_ITEM, pipelineRef: 'contract:k-1:manual', reservationState: 'reserved' }
  const editContractLabel = () => i18n.t('stockDetail.configuration.editContract')

  it('is offered on a car already in stock', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderItem({ ...PIPELINE_ITEM, lifecycleStatus: 'in_stock', vin: CONFIGURED_VIN, inStockAt: NOW })

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).getByRole('button', { name: editLabel() })).toBeEnabled()
    expect(screen.queryByRole('button', { name: editContractLabel() })).toBeNull()
  })

  it('is offered on an unreserved pipeline car a contract created (a trade-in)', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderItem({ ...PIPELINE_ITEM, pipelineRef: 'contract:k-1:trade_in' })

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).getByRole('button', { name: editLabel() })).toBeEnabled()
  })

  it.each([
    ['a pipeline car its signed contract reserves', RESERVED],
    ['a car in stock a signed contract reserves', { ...PIPELINE_ITEM, lifecycleStatus: 'in_stock', vin: CONFIGURED_VIN, inStockAt: NOW, reservationState: 'reserved' } as StockItemRead],
  ])('on %s: «Vertrag bearbeiten» instead, disabled, with the reason', async (_case, item) => {
    configurationBackend(configurationRead(RECORDED))
    renderItem(item)

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).queryByRole('button', { name: editLabel() })).toBeNull()
    const editContract = screen.getByRole('button', { name: editContractLabel() })
    expect(editContract).toBeDisabled()
    const reason = i18n.t('stockDetail.configuration.reservedByContract')
    expect(screen.getByText(reason)).toBeInTheDocument()
    expect(editContract).toHaveAccessibleDescription(reason)
  })

  it('is not offered on a car that has left stock (pipeline or in stock only)', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderItem({ ...PIPELINE_ITEM, lifecycleStatus: 'in_stock', vin: CONFIGURED_VIN, inStockAt: NOW, leftStockAt: NOW })

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).queryByRole('button', { name: editLabel() })).toBeNull()
    expect(screen.queryByRole('button', { name: editContractLabel() })).toBeNull()
  })

  it('offers neither to a role that may not write configurations, reserved or not', async () => {
    configurationBackend(configurationRead(RECORDED))
    renderItem(RESERVED, { canEditConfiguration: false })

    const card = await screen.findByTestId('configuration-summary-card')
    expect(within(card).queryByRole('button', { name: editLabel() })).toBeNull()
    expect(screen.queryByRole('button', { name: editContractLabel() })).toBeNull()
    expect(screen.queryByText(i18n.t('stockDetail.configuration.reservedByContract'))).toBeNull()
  })

  it('opens the configuration in its own mode, and its PATCH is the only write', async () => {
    const user = userEvent.setup()
    const { backend, patches } = configurationBackend(configurationRead(RECORDED))
    renderDetails(true)

    const card = await screen.findByTestId('configuration-summary-card')
    await user.click(within(card).getByRole('button', { name: editLabel() }))

    const vin = await screen.findByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })
    // Its own mode only: no mode switch, no way back to a new car.
    expect(screen.queryByRole('radiogroup', { name: i18n.t('configurator.mode.label') })).toBeNull()
    expect(screen.queryByRole('button', { name: i18n.t('configurator.configure.back') })).toBeNull()
    expect((vin as HTMLInputElement).value).toBe(CONFIGURED_VIN)

    await user.clear(vin)
    await user.type(vin, CORRECTED_VIN)
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(patches).toHaveLength(1))
    expect(patches[0]).toMatchObject({ vin: CORRECTED_VIN, mode: 'record' })
    // The overlay closes and the card shows the configuration's answer.
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })).toBeNull(),
    )
    const writes = backend.calls.filter((c) => c.method !== 'GET')
    expect(writes.map((c) => `${c.method} ${c.pathname}`).filter((w) => !w.startsWith('PATCH /configurations/cfg-1'))).toEqual([])
    expect(backend.calls.some((c) => c.pathname.startsWith('/inventory/'))).toBe(false)
  })

  it('on a pipeline car its signed contract reserves, the purchase refusal offers no edit either', async () => {
    const user = userEvent.setup()
    const purchaseRefused = new ApiError(422, 'unprocessable_entity', 'refused', { field: 'vin', reason: 'configuration_vin_differs' })
    configurationBackend(configurationRead(RECORDED))
    renderItem(RESERVED, { onRecordPurchase: vi.fn().mockRejectedValueOnce(purchaseRefused) })

    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.title') }))
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') }))
    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
    await user.clear(vinField())
    await user.type(vinField(), CORRECTED_VIN)
    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('stockDetail.purchase.configurationVinDiffers'))
    expect(within(alert).queryByRole('button', { name: editLabel() })).toBeNull()
  })

  it('from the purchase refusal: corrects the configuration, then returns to the booking with the corrected VIN', async () => {
    const user = userEvent.setup()
    const purchaseRefused = new ApiError(422, 'unprocessable_entity', 'refused', { field: 'vin', reason: 'configuration_vin_differs' })
    const onRecordPurchase = vi.fn().mockRejectedValueOnce(purchaseRefused).mockResolvedValueOnce(undefined)
    const { patches } = configurationBackend(configurationRead(RECORDED))
    renderDetails(true, onRecordPurchase)

    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.title') }))
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') }))
    await waitFor(() => expect(vinField().value).toBe(CONFIGURED_VIN))
    await user.clear(vinField())
    await user.type(vinField(), CORRECTED_VIN)
    await user.type(field('stockDetail.purchase.fields.supplierName'), 'Hans Muster')
    await user.type(field('stockDetail.purchase.fields.purchasePrice'), '8000')
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))

    const alert = await screen.findByRole('alert')
    await user.click(within(alert).getByRole('button', { name: editLabel() }))

    // The configurator, on the stored configuration; the dialog stepped aside.
    const vin = await screen.findByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })
    await waitFor(() => expect(screen.queryByRole('button', { name: i18n.t('stockDetail.purchase.submit') })).toBeNull())
    await user.clear(vin)
    await user.type(vin, CORRECTED_VIN)
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(patches).toHaveLength(1))

    // Back in the booking: what was entered is kept, the VIN is the corrected one, the refusal is gone.
    await waitFor(() => expect(vinField().value).toBe(CORRECTED_VIN))
    expect((field('stockDetail.purchase.fields.supplierName') as HTMLInputElement).value).toBe('Hans Muster')
    expect(screen.queryByRole('alert')).toBeNull()

    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))
    await waitFor(() => expect(onRecordPurchase).toHaveBeenCalledTimes(2))
    expect(onRecordPurchase.mock.calls[1][0]).toMatchObject({ vin: CORRECTED_VIN, supplierName: 'Hans Muster' })
  })
})

// KAN-557 — on a car with a vehicle-mdm record (bought), the VIN is Stock's
// «VIN korrigieren»: the configurator's PATCH leaves `vin` out and hands a
// changed VIN to that action, which corrects the stock item, the record and
// the configuration together. A pipeline car without a record keeps writing
// its VIN in the PATCH (it wins at booking, KAN-575).
describe('Stock detail — the configurator routes a bought car\'s VIN through «VIN korrigieren» (KAN-557)', () => {
  const BOUGHT_ITEM: StockItemRead = {
    ...PIPELINE_ITEM,
    lifecycleStatus: 'in_stock',
    vin: CONFIGURED_VIN,
    vehicleId: 'v-557',
    inStockAt: NOW,
    purchasePrice: '8000.00',
    version: 7,
  }
  const configuratorVin = () =>
    screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) }) as HTMLInputElement

  function boughtBackend(item: StockItemRead, configuration: Partial<ConfigurationRead> = {}) {
    const patches: Record<string, unknown>[] = []
    let current = configurationRead({ ...RECORDED, vehicleId: item.vehicleId, ...configuration })
    let stock = item
    const backend = installFakeBackend([
      ...CODED_REFERENCE_ROUTES,
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'GET', match: /^\/vehicle-mdm\/v-557$/, handler: () => ({ id: 'v-557', vehicleNumber: 'F-000557', vin: current.vin }) },
      { method: 'GET', match: /^\/configurations\/cfg-1$/, handler: () => current },
      { method: 'GET', match: /^\/inventory\/stock-items\/s1$/, handler: () => stock },
      {
        method: 'PATCH',
        match: /^\/configurations\/cfg-1$/,
        handler: (req) => {
          const body = req.body as Record<string, unknown>
          patches.push(body)
          current = { ...current, ...('vin' in body ? { vin: (body.vin as string | null) ?? null } : {}), version: current.version + 1 }
          // Stock's label refresh may bump the item meanwhile.
          stock = { ...stock, version: stock.version + 1 }
          return current
        },
      },
      {
        method: 'PATCH',
        match: /^\/configurations\/cfg-1\/options$/,
        handler: () => {
          current = { ...current, version: current.version + 1 }
          return current
        },
      },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/s1\/vin-correction$/,
        handler: (req) => {
          const body = req.body as { newVin: string }
          current = { ...current, vin: body.newVin, version: current.version + 1 }
          stock = { ...stock, vin: body.newVin, version: stock.version + 1 }
          return stock
        },
      },
    ])
    return { backend, patches, configuration: () => current }
  }

  const renderStock = (item: StockItemRead, canCorrectVin: boolean) =>
    renderWithProviders(
      <DetailsTab
        item={item}
        locale="de-CH"
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        canEditConfiguration
        canCorrectVin={canCorrectVin}
        onRecordPurchase={vi.fn()}
      />,
    )

  async function openConfigurator(user: ReturnType<typeof userEvent.setup>) {
    const card = await screen.findByTestId('configuration-summary-card')
    await user.click(within(card).getByRole('button', { name: editLabel() }))
    await screen.findByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })
  }

  it('on a bought car: the PATCH carries no vin, then «VIN korrigieren» POSTs the new one, and the overlay closes', async () => {
    const user = userEvent.setup()
    const { backend, patches, configuration } = boughtBackend(BOUGHT_ITEM)
    renderStock(BOUGHT_ITEM, true)
    await openConfigurator(user)

    await user.clear(configuratorVin())
    await user.type(configuratorVin(), CORRECTED_VIN.toLowerCase())
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    // The dialog opens over the configurator, prefilled, after the configuration's own save.
    const dialog = await screen.findByRole('dialog', { name: i18n.t('stockDetail.vinCorrection.title') })
    expect(patches).toHaveLength(1)
    expect(patches[0]).not.toHaveProperty('vin')
    expect(patches[0]).toMatchObject({ mode: 'record' })
    const newVin = within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.newVinLabel')) })
    expect((newVin as HTMLInputElement).value).toBe(CORRECTED_VIN)
    expect(within(dialog).getByText(i18n.t('stockDetail.vinCorrection.body', { stockNumber: 'S-000510' }))).toBeInTheDocument()

    await user.type(
      within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.reasonLabel')) }),
      'Tippfehler',
    )
    await user.click(within(dialog).getByRole('button', { name: i18n.t('stockDetail.vinCorrection.submit') }))

    await waitFor(() => expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/vin-correction$/, 'POST')
    expect(post.body).toEqual({ newVin: CORRECTED_VIN, reason: 'Tippfehler' })
    // The stock item's version as the server holds it after the configuration's save.
    expect(post.headers.get('If-Match')).toBe('8')
    // The overlay re-read the configuration and closed.
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })).toBeNull(),
    )
    expect(configuration().vin).toBe(CORRECTED_VIN)
    const writes = backend.calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.pathname}`)
    expect(writes[0]).toBe('PATCH /configurations/cfg-1')
    expect(writes[writes.length - 1]).toBe('POST /inventory/stock-items/s1/vin-correction')
  })

  it('on an oldtimer bought under its chassis number: a corrected chassis number goes to «VIN korrigieren» (KAN-535)', async () => {
    const user = userEvent.setup()
    const chassis = '1130101234567'
    const corrected = '1130101234568'
    const oldtimer: StockItemRead = { ...BOUGHT_ITEM, vin: chassis }
    // Its configuration carries the chassis number with a first registration before 1981 (vehicleId stays empty:
    // a chassis number never links a record, KAN-353).
    const { backend, patches } = boughtBackend(oldtimer, { vin: chassis, vehicleId: null, firstRegistrationDate: '1968-05-02' })
    renderStock(oldtimer, true)
    await openConfigurator(user)

    await user.clear(configuratorVin())
    await user.type(configuratorVin(), corrected)
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    const dialog = await screen.findByRole('dialog', { name: i18n.t('stockDetail.vinCorrection.title') })
    expect(patches).toHaveLength(1)
    expect(patches[0]).not.toHaveProperty('vin')
    const newNumber = within(dialog).getByRole('textbox', {
      name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.newChassisNumberLabel')),
    })
    expect((newNumber as HTMLInputElement).value).toBe(corrected)
    await user.type(
      within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.reasonLabel')) }),
      'Tippfehler',
    )
    await user.click(within(dialog).getByRole('button', { name: i18n.t('stockDetail.vinCorrection.submit') }))

    await waitFor(() => expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/vin-correction$/, 'POST')[0].body).toEqual({ newVin: corrected, reason: 'Tippfehler' })
  })

  it('on a bought car: a cancelled correction keeps the configurator open, the other fields saved', async () => {
    const user = userEvent.setup()
    const { backend, patches } = boughtBackend(BOUGHT_ITEM)
    renderStock(BOUGHT_ITEM, true)
    await openConfigurator(user)

    await user.clear(configuratorVin())
    await user.type(configuratorVin(), CORRECTED_VIN)
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('stockDetail.vinCorrection.title') })
    await user.click(within(dialog).getByRole('button', { name: i18n.t('stockDetail.vinCorrection.cancel') }))

    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.notCorrected'))).toBeInTheDocument()
    expect(configuratorVin().value).toBe(CORRECTED_VIN)
    expect(patches).toHaveLength(1)
    expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(0)
  })

  it('on a bought car: an emptied VIN is refused in place, nothing sent', async () => {
    const user = userEvent.setup()
    const { backend, patches } = boughtBackend(BOUGHT_ITEM)
    renderStock(BOUGHT_ITEM, true)
    await openConfigurator(user)

    expect(screen.queryByRole('button', { name: i18n.t('configurator.vehicleLink.clearVin') })).toBeNull()
    await user.clear(configuratorVin())
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.configuratorClearRefused'))).toBeInTheDocument()
    expect(patches).toHaveLength(0)
    expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(0)
  })

  it('on a bought car, without the stock_vin_correction row: the VIN is read-only, the hint says why; saving sends no vin', async () => {
    const user = userEvent.setup()
    const { backend, patches } = boughtBackend(BOUGHT_ITEM)
    renderStock(BOUGHT_ITEM, false)
    await openConfigurator(user)

    const vin = configuratorVin()
    expect(vin).toHaveAttribute('readonly')
    expect(vin).toHaveAccessibleDescription(i18n.t('stockDetail.vinCorrection.configuratorReadOnly'))
    await user.type(vin, 'X')
    expect(vin.value).toBe(CONFIGURED_VIN)

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(patches).toHaveLength(1))
    expect(patches[0]).not.toHaveProperty('vin')
    expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(0)
  })

  it('on a pipeline car without a vehicle-mdm record: the PATCH carries the VIN, no correction is POSTed', async () => {
    const user = userEvent.setup()
    const { backend, patches } = boughtBackend({ ...PIPELINE_ITEM, vehicleId: null })
    renderStock({ ...PIPELINE_ITEM, vehicleId: null }, true)
    await openConfigurator(user)

    await user.clear(configuratorVin())
    await user.type(configuratorVin(), CORRECTED_VIN)
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(patches).toHaveLength(1))
    expect(patches[0]).toMatchObject({ vin: CORRECTED_VIN })
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })).toBeNull(),
    )
    expect(backend.callsTo(/\/vin-correction$/, 'POST')).toHaveLength(0)
  })
})
