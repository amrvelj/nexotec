// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { configurationRead } from '../test/configuratorFixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// C-F (KAN-10) exit criteria 1, 2 and 4 in the real offer host:
// - Path B opens the configurator as an overlay (ADR-059) and the half-built
//   offer underneath survives, address bar untouched;
// - Path B is build-only — no record mode is reachable there;
// - the trade-in goes through the valuation path in record mode and reaches
//   the offer as a valuation reference;
// - every configuration on the offer renders through the one summary card.

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

function draftOffer(overrides: Partial<SalesOfferRead> = {}): SalesOfferRead {
  return {
    id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', customerId: null, customerLabel: null,
    customerLocality: null, vehicleSource: null, stockItemId: null, vehicleLabel: null, manualVehicleCondition: null,
    manualBasePrice: null, leasingDownPayment: null, leasingTermMonths: null, leasingKmPerYear: null, basePrice: null,
    optionsTotal: null, listPrice: null, accessoriesTotal: null, totalBeforeDiscount: null, discountType: null,
    discountValue: null, discountAmount: null, grossPrice: null, costBasis: null, margin: null,
    vehicleSnapshotFrozenAt: null, tradeInVehicleId: null, tradeInLabel: null, tradeInVin: null,
    tradeInValuationId: null, tradeInValue: null, tradeInPurchasePrice: null, payable: null, cancelledReason: null,
    copiedFromOfferId: null,
    containers: [
      { id: 'customer', requirement: 'required', status: 'not_started' },
      { id: 'vehicle', requirement: 'required', status: 'not_started' },
      { id: 'pricing', requirement: 'required', status: 'not_started' },
      { id: 'trade_in', requirement: 'optional', status: 'not_started' },
      { id: 'leasing', requirement: 'optional', status: 'not_started' },
    ],
    vehicleCondition: null, version: 1, createdAt: '2026-02-01T00:00:00Z', updatedAt: '2026-02-01T00:00:00Z',
    ...overrides,
  }
}

const CATALOGUE: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
  { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
  { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
]

function LocationProbe() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

function render() {
  return renderWithProviders(
    <>
      <LocationProbe />
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>
    </>,
    { route: '/sales/offers/o1' },
  )
}

async function typeHalfBuiltTradeIn(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel')), 'ZFFSG17A0H0071234')
}

describe('OfferWorkspace — Path B through the configurator (FR-C-12)', () => {
  it('opens build-only as an overlay, keeps the half-built offer, and renders the summary card', async () => {
    const user = userEvent.setup()
    let offer = draftOffer()
    const patches: unknown[] = []
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => offer },
      {
        method: 'PATCH',
        match: /^\/sales\/offers\/o1$/,
        handler: (req) => {
          patches.push(req.body)
          offer = draftOffer({
            ...offer, version: offer.version + 1, configurationId: 'cfg-1',
            configurationLabel: 'Volkswagen Golf Golf GTI', vehicleSource: 'manual', vehicleLabel: 'Volkswagen Golf Golf GTI',
          })
          return offer
        },
      },
      {
        method: 'POST',
        match: /^\/configurations$/,
        handler: () => ({ __status: 201, body: configurationRead({ source: 'manual', catalogueVariantId: null }) }),
      },
      { method: 'GET', match: /^\/configurations\/cfg-1$/, handler: () => configurationRead() },
      ...CATALOGUE,
    ])
    render()

    await typeHalfBuiltTradeIn(user)
    await user.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.vehicle.configure') }))
    const overlay = await screen.findByRole('dialog')
    expect(within(overlay).getByText(i18n.t('configurator.find.title'))).toBeInTheDocument()
    // Mode matrix: no record mode in this host.
    expect(within(overlay).queryByText(i18n.t('configurator.mode.record'))).not.toBeInTheDocument()
    expect(within(overlay).queryByLabelText(i18n.t('configurator.mode.label'))).not.toBeInTheDocument()
    // Not a navigation, and the half-built offer is still there.
    expect(screen.getByTestId('path')).toHaveTextContent('/sales/offers/o1')
    expect(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel'))).toHaveValue('ZFFSG17A0H0071234')

    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(patches).toEqual([{ configurationId: 'cfg-1' }])
    const container = await screen.findByTestId('offer-vehicle-configured')
    expect(await within(container).findByTestId('configuration-summary-card')).toBeInTheDocument()
    expect(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel'))).toHaveValue('ZFFSG17A0H0071234')
    expect(screen.getByTestId('path')).toHaveTextContent('/sales/offers/o1')
  })

  it('closing the configurator without saving leaves the offer exactly as it was', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => draftOffer() },
      ...CATALOGUE,
    ])
    render()

    await typeHalfBuiltTradeIn(user)
    await user.click(screen.getByRole('button', { name: i18n.t('offerWorkspace.vehicle.configure') }))
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel'))).toHaveValue('ZFFSG17A0H0071234')
    expect(backend.calls.some((c) => c.method !== 'GET')).toBe(false)
  })
})

describe('OfferWorkspace — the trade-in through the valuation path (FR-C-12 carve-out)', () => {
  it('captures the trade-in in record mode, values it, and the offer references the valuation', async () => {
    const user = userEvent.setup()
    let offer = draftOffer()
    const valuationBodies: Record<string, unknown>[] = []
    const attached: unknown[] = []
    const attachHeaders: Headers[] = []
    const tradeIn = configurationRead({
      id: 'cfg-ti', mode: 'record', source: 'manual', catalogueVariantId: null,
      brandDisplayName: 'Subaru', modelGroupName: 'Justy', variantName: 'G3X', licencePlate: 'ZH123456', mileageKm: 148000,
    })
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => offer },
      { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: tradeIn }) },
      { method: 'GET', match: /^\/configurations\/cfg-ti$/, handler: () => tradeIn },
      {
        method: 'POST',
        match: /^\/valuations$/,
        handler: (req) => {
          valuationBodies.push(req.body as Record<string, unknown>)
          return { __status: 201, body: { id: 'val-7' } }
        },
      },
      {
        method: 'POST',
        match: /^\/sales\/offers\/o1\/trade-in\/valuation$/,
        handler: (req) => {
          attached.push(req.body)
          attachHeaders.push(req.headers)
          offer = draftOffer({
            ...offer, version: 2, tradeInValuationId: 'val-7', tradeInConfigurationId: 'cfg-ti',
            tradeInLabel: 'Subaru Justy G3X', tradeInValue: '2500.00',
          })
          return offer
        },
      },
      { method: 'GET', match: /^\/valuations\/val-7$/, handler: () => ({ id: 'val-7', source: 'manual' }) },
      ...CATALOGUE,
    ])
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.valueWithConfigurator') }))
    const overlay = await screen.findByRole('dialog')
    // Record only: the build mode is not reachable for the customer's car.
    expect(within(overlay).queryByText(i18n.t('configurator.mode.build'))).not.toBeInTheDocument()
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))

    // The valuation dialog opens on the workspace, prefilled from the configuration.
    const valuationDialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(within(valuationDialog).getByLabelText(i18n.t('valuationCreate.plate'))).toHaveValue('ZH123456')
    expect(within(valuationDialog).getByTestId('configuration-summary-card')).toBeInTheDocument()
    await user.type(within(valuationDialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false }), '2500')
    await user.click(within(valuationDialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))

    await waitFor(() => expect(attached).toEqual([{ valuationId: 'val-7' }]))
    // KAN-266 — the attach carries an Idempotency-Key beside its If-Match.
    expect(attachHeaders[0].get('Idempotency-Key')).toBeTruthy()
    expect(attachHeaders[0].get('If-Match')).toBe('1')
    expect(valuationBodies[0]).toMatchObject({ configurationId: 'cfg-ti', vehiclePlate: 'ZH123456' })
    expect(await screen.findByText('Subaru Justy G3X')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('configuration-summary-card').length).toBeGreaterThan(0))
  })
})
