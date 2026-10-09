// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { configurationRead } from '../../test/configuratorFixtures'
import { toSwissLocale } from '../../i18n'
import type { StockItemRead } from '../../api/types'
import { DetailsTab } from '../../components/stock-detail/DetailsTab'
import { StockListPage } from '../StockListPage'

// C-F (KAN-10, FR-C-13) — the Stock host. "Add to pipeline" opens the
// configurator as an overlay with BOTH modes (build for a factory order,
// record for a car being bought in), and the stock item it creates carries
// the configuration, which its detail renders through the one summary card.

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

const CATALOGUE: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
  { method: 'GET', match: /^\/inventory\/stock-items$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
]

function LocationProbe() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

describe('Stock list — add to pipeline (FR-C-13)', () => {
  it('offers both modes and creates a pipeline item carrying the configuration', async () => {
    const user = userEvent.setup()
    const created: Record<string, unknown>[] = []
    installFakeBackend([
      {
        method: 'POST',
        match: /^\/configurations$/,
        handler: (req) => ({
          __status: 201,
          body: configurationRead({
            id: 'cfg-r', mode: (req.body as { mode: 'build' | 'record' }).mode, source: 'manual', catalogueVariantId: null,
            brandDisplayName: 'Subaru', modelGroupName: 'Justy', variantName: 'G3X', mileageKm: 148000,
          }),
        }),
      },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items$/,
        handler: (req) => {
          created.push(req.body as Record<string, unknown>)
          return { __status: 201, body: { id: 'si-9', stockNumber: 'S-00099' } }
        },
      },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <>
        <LocationProbe />
        <StockListPage />
      </>,
      { route: '/stock' },
    )

    // A half-typed search on the list underneath must survive the overlay.
    const search = await screen.findByPlaceholderText(i18n.t('stockList.searchPlaceholder'))
    await user.type(search, 'Justy')
    await user.click(screen.getByRole('button', { name: i18n.t('stockList.addToPipeline') }))
    const overlay = await screen.findByRole('dialog')
    expect(screen.getByPlaceholderText(i18n.t('stockList.searchPlaceholder'))).toHaveValue('Justy')
    expect(screen.getByTestId('path')).toHaveTextContent('/stock')
    expect(within(overlay).getByText(i18n.t('configurator.mode.build'))).toBeInTheDocument()
    expect(within(overlay).getByText(i18n.t('configurator.mode.record'))).toBeInTheDocument()

    await user.click(within(overlay).getByText(i18n.t('configurator.mode.record')))
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(created).toHaveLength(1))
    expect(created[0]).toMatchObject({
      vehicleLabel: 'Subaru Justy G3X', condition: 'used', configurationId: 'cfg-r', odometerKm: 148000,
    })
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/stock/si-9'))
  })

  it('keeps the overlay open with the reason when the pipeline item cannot be created', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'POST',
        match: /^\/configurations$/,
        handler: () => ({ __status: 201, body: configurationRead({ source: 'manual', catalogueVariantId: null }) }),
      },
      { method: 'POST', match: /^\/inventory\/stock-items$/, handler: () => status(500, { error: { code: 'x', message: 'x' } }) },
      ...CATALOGUE,
    ])
    renderWithProviders(<StockListPage />, { route: '/stock' })

    await user.click(await screen.findByRole('button', { name: i18n.t('stockList.addToPipeline') }))
    const overlay = await screen.findByRole('dialog')
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))

    expect(await within(overlay).findByText(i18n.t('stockList.addToPipelineError'))).toBeInTheDocument()
  })

  it('a pipeline item retried after a failure carries the same Idempotency-Key (KAN-266)', async () => {
    // The first create fails (had its response been lost, the server would
    // hold the item): saving again re-sends the same request under the same
    // key, so the server replays instead of adding the car twice.
    const user = userEvent.setup()
    const config = configurationRead({ source: 'manual', catalogueVariantId: null })
    let attempts = 0
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: config }) },
      { method: 'PATCH', match: /^\/configurations\/[^/]+$/, handler: () => config },
      { method: 'PATCH', match: /^\/configurations\/[^/]+\/options$/, handler: () => config },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
          return { __status: 201, body: { id: 'si-9' } }
        },
      },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <>
        <StockListPage />
        <LocationProbe />
      </>,
      { route: '/stock' },
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('stockList.addToPipeline') }))
    const overlay = await screen.findByRole('dialog')
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))
    expect(await within(overlay).findByText(i18n.t('stockList.addToPipelineError'))).toBeInTheDocument()
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/stock/si-9'))

    const keys = backend.callsTo(/^\/inventory\/stock-items$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBe(keys[0])
  })
})

describe('Stock list — add to pipeline, one key per opening (KAN-266)', () => {
  it('the same pipeline item sent after closing and reopening the configurator gets a new key', async () => {
    // Without the renew on opening, the key of a failed create would follow
    // the button to the next opening and replay whatever it was bound to.
    const user = userEvent.setup()
    const config = configurationRead({ source: 'manual', catalogueVariantId: null })
    let attempts = 0
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: config }) },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
          return { __status: 201, body: { id: 'si-9' } }
        },
      },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <>
        <StockListPage />
        <LocationProbe />
      </>,
      { route: '/stock' },
    )
    const addThroughANewConfiguration = async () => {
      await user.click(await screen.findByRole('button', { name: i18n.t('stockList.addToPipeline') }))
      const overlay = await screen.findByRole('dialog')
      await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
      await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))
      return overlay
    }

    const overlay = await addThroughANewConfiguration()
    expect(await within(overlay).findByText(i18n.t('stockList.addToPipelineError'))).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await addThroughANewConfiguration()
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/stock/si-9'))

    const keys = backend.callsTo(/^\/inventory\/stock-items$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(2)
    expect(backend.callsTo(/^\/inventory\/stock-items$/, 'POST')[1].body).toEqual(
      backend.callsTo(/^\/inventory\/stock-items$/, 'POST')[0].body,
    )
    expect(keys[1]).toBeTruthy()
    expect(keys[1]).not.toBe(keys[0])
  })
})

describe('Stock detail — a pipeline item shows its configuration', () => {
  it('renders the shared summary card for the configuration the item points at', async () => {
    installFakeBackend([{ method: 'GET', match: /^\/configurations\/cfg-r$/, handler: () => configurationRead({ id: 'cfg-r' }) }])
    const item = {
      id: 'si-9', stockNumber: 'S-00099', vehicleLabel: 'Subaru Justy G3X', vehicleId: null, vin: null,
      listPrice: null, effectivePrice: null, firstRegistrationDate: null, odometerKm: null, isInvoiceable: false,
      purchaseDate: null, purchasePrice: null, supplierName: null, notionalInputTaxAmount: null,
      notionalInputTaxApplicable: null, configurationId: 'cfg-r', configurationLabel: 'Subaru Justy G3X',
    } as unknown as StockItemRead
    renderWithProviders(
      <DetailsTab
        item={item}
        locale={toSwissLocale('de')}
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        onRecordPurchase={vi.fn()}
      />,
    )

    expect(await screen.findByText(i18n.t('stockDetail.configuration.title'))).toBeInTheDocument()
    expect(await screen.findByTestId('configuration-summary-card')).toBeInTheDocument()
  })
})
