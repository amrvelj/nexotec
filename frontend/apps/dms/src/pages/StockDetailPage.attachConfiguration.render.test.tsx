// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, configurationRead, pickCodedFields } from '../test/configuratorFixtures'
import type { StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'

// KAN-463 — a stock item the retired hand-typed intake created has no
// configuration and so no technical data. Its detail offers «Konfiguration
// zuordnen»: the configurator opens (both modes, `record` first, the car's
// VIN prefilled), and its commit links the configuration with If-Match and
// an Idempotency-Key; the technical data card replaces the offer.

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

const ID = 'st-hand-typed'
const VIN = 'WVWZZZ1KZAW000001'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000002', vehicleLabel: 'VW Golf 8 GTI', vin: VIN, vehicleId: null, condition: 'used',
  lifecycleStatus: 'in_stock', reservationState: 'none', bodyStyle: null, exteriorColour: null, odometerKm: null,
  firstRegistrationDate: null, basePrice: null, listPrice: null, effectivePrice: null, landedCost: null,
  notionalInputTaxApplicable: null, notionalInputTaxRate: null, notionalInputTaxAmount: null,
  notionalInputTaxOverridden: false, purchasePrice: null, purchaseDate: null, purchaseInvoiceRef: null,
  supplierName: null, supplierIsVatRegistered: null, orderDate: null, expectedDelivery: null, inStockAt: null,
  leftStockAt: null, pipelineRef: null, locationId: null, valuationRefId: null, valuationRefSource: null,
  valuationRefAmount: null, valuationRefValuedAt: null, isInvoiceable: false, version: 3,
  configurationId: null, configurationLabel: null,
  createdAt: '2026-10-26T00:00:00Z', updatedAt: '2026-10-26T00:00:00Z',
}
const CONFIGURATION = configurationRead({
  id: 'cfg-attached', mode: 'record', source: 'manual', catalogueVariantId: null, vin: VIN,
  brandDisplayName: 'Volkswagen', modelGroupName: 'Golf', variantName: '8 GTI',
})

const CONFIGURATOR: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  ...CODED_REFERENCE_ROUTES,
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
  { method: 'GET', match: /^\/vehicle-identification$/, handler: () => ({ hits: [] }) },
  { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: CONFIGURATION }) },
  { method: 'GET', match: /^\/configurations\/cfg-attached$/, handler: () => CONFIGURATION },
]

function install(attach: () => unknown) {
  return installFakeBackend([
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => ITEM },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
    { method: 'POST', match: new RegExp(`^/inventory/stock-items/${ID}/configuration$`), handler: attach },
    ...CONFIGURATOR,
  ])
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/stock/:id" element={<StockDetailPage />} />
    </Routes>,
    { route: `/stock/${ID}` },
  )
}

async function attachThroughTheConfigurator(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.configuration.attach') }))
  const overlay = await screen.findByRole('dialog')
  await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
  await pickCodedFields()
  await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))
  return overlay
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('StockDetailPage — «Konfiguration zuordnen» (KAN-463)', () => {
  it('says the configuration is missing and opens the configurator in record mode with the VIN', async () => {
    const user = userEvent.setup()
    install(() => ({ ...ITEM, version: 4, configurationId: 'cfg-attached' }))
    render()

    expect(await screen.findByText(i18n.t('stockDetail.configuration.missing'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.configuration.attach') }))

    const overlay = await screen.findByRole('dialog')
    expect(within(overlay).getByText(i18n.t('configurator.mode.build'))).toBeInTheDocument()
    expect(within(overlay).getByRole('radio', { name: i18n.t('configurator.mode.record') })).toBeChecked()
    expect(within(overlay).getByDisplayValue(VIN)).toBeInTheDocument()
  })

  it('links the saved configuration with If-Match and a key, then shows its technical data', async () => {
    const user = userEvent.setup()
    const backend = install(() => ({ ...ITEM, version: 4, configurationId: 'cfg-attached', configurationLabel: 'Volkswagen Golf 8 GTI' }))
    render()

    await attachThroughTheConfigurator(user)

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const [call] = backend.callsTo(new RegExp(`^/inventory/stock-items/${ID}/configuration$`), 'POST')
    expect(call.body).toEqual({ configurationId: 'cfg-attached' })
    expect(call.headers.get('If-Match')).toBe('3')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(i18n.t('stockDetail.configuration.missing'))).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.configuration.attach') })).not.toBeInTheDocument()
    expect(backend.callsTo(/^\/configurations\/cfg-attached$/, 'GET').length).toBeGreaterThan(0)
  })

  it('keeps the configurator open with the refusal in the UI language', async () => {
    const user = userEvent.setup()
    install(() =>
      status(409, {
        error: {
          code: 'conflict',
          message: 'This stock item already has a configuration.',
          details: { reason: 'configuration_already_linked', configurationId: 'cfg-other' },
        },
      }),
    )
    render()

    const overlay = await attachThroughTheConfigurator(user)

    expect(await within(overlay).findByText(i18n.t('apiRefusal.reasons.configuration_already_linked'))).toBeInTheDocument()
    expect(within(overlay).queryByText('This stock item already has a configuration.')).not.toBeInTheDocument()
  })
})
