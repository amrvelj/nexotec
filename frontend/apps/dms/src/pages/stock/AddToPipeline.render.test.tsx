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

    await user.click(await screen.findByRole('button', { name: i18n.t('stockList.addToPipeline') }))
    const overlay = await screen.findByRole('dialog')
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
