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
