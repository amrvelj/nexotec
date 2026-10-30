// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'

// KAN-430 (Anto, 2026-10-30, option A) — since KAN-376 a salesperson creates
// stock items and lands on their detail, which offered the purchase booking,
// the cost booking and the publishing their role may not use, and ended in a
// 403. An action the role may never use is now hidden; the session read says
// which matrix rows the user may write (`writableCapabilities`).

const ID = 'st-role'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000010', vehicleLabel: 'Škoda Octavia Combi', vin: null, vehicleId: null, condition: 'used',
  lifecycleStatus: 'pipeline', reservationState: 'none', bodyStyle: null, exteriorColour: null, odometerKm: null,
  firstRegistrationDate: null, basePrice: null, listPrice: null, effectivePrice: null, landedCost: null,
  notionalInputTaxApplicable: null, notionalInputTaxRate: null, notionalInputTaxAmount: null,
  notionalInputTaxOverridden: false, purchasePrice: null, purchaseDate: null, purchaseInvoiceRef: null,
  supplierName: null, supplierIsVatRegistered: null, orderDate: null, expectedDelivery: null, inStockAt: null,
  leftStockAt: null, pipelineRef: null, locationId: null, valuationRefId: null, valuationRefSource: null,
  valuationRefAmount: null, valuationRefValuedAt: null, isInvoiceable: false, version: 1,
  configurationId: null, configurationLabel: null,
  createdAt: '2026-10-30T00:00:00Z', updatedAt: '2026-10-30T00:00:00Z',
}

const publishing = (channel: string) => ({
  id: `pub-${channel}`, stockItemId: ID, channel, state: 'not_published', transmissionStatus: 'not_sent',
  lastPublishedAt: null, lastAttemptedAt: null, lastTransmissionError: null, zusatztitel: null, bemerkungen: null,
  zustandsbeschreibung: null, haendlerbemerkungen: null, youtubeUrl: null, pdfDocumentRef: null, version: 1,
  blockingConditions: [],
})

function session(writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Sara', lastName: 'Verkauf', email: 'sara@example.ch', accessRoles: ['sales'] },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function install(writableCapabilities: string[]) {
  installFakeBackend([
    session(writableCapabilities),
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => ITEM },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
    {
      method: 'GET',
      match: new RegExp(`^/inventory/stock-items/${ID}/publishing/(\\w+)$`),
      handler: (req) => publishing(req.pathname.split('/').pop() as string),
    },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/media$`), handler: () => [] },
    {
      method: 'GET',
      match: new RegExp(`^/inventory/stock-items/${ID}/equipment$`),
      handler: () => ({ ausstattungCodes: [], extras: ['Anhängerkupplung'], eigenschaften: [], providerAusstattung: {} }),
    },
  ])
}

function render(tab?: string) {
  renderWithProviders(
    <Routes>
      <Route path="/stock/:id" element={<StockDetailPage />} />
    </Routes>,
    { route: tab ? `/stock/${ID}?tab=${tab}` : `/stock/${ID}` },
  )
}

const SALES = ['stock_items', 'stock_condition']
const INVENTORY = ['stock_items', 'stock_condition', 'stock_acquisition', 'marketplace_publishing']

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('StockDetailPage — what the role may never use is hidden (KAN-430)', () => {
  it('shows a salesperson no purchase booking, only that none is recorded', async () => {
    const user = userEvent.setup()
    install(SALES)
    render()

    await user.click(await screen.findByText(i18n.t('stockDetail.purchase.title')))

    expect(await screen.findByText(i18n.t('stockDetail.purchase.notRecorded'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') })).not.toBeInTheDocument()
  })

  it('offers the purchase booking to a role that may book it', async () => {
    const user = userEvent.setup()
    install(INVENTORY)
    render()

    await user.click(await screen.findByText(i18n.t('stockDetail.purchase.title')))

    expect(await screen.findByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') })).toBeInTheDocument()
  })

  it('shows a salesperson the Wagenbuch without the cost booking', async () => {
    install(SALES)
    render('wagenbuch')

    expect(await screen.findByText(i18n.t('stockDetail.wagenbuch.empty'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.wagenbuch.recordButton') })).not.toBeInTheDocument()
  })

  it('shows a salesperson each channel\'s status and the equipment, without publishing, listing texts or photos', async () => {
    install(SALES)
    render('publishing')

    expect(await screen.findByText('Anhängerkupplung')).toBeInTheDocument()
    expect((await screen.findAllByText(i18n.t('stockDetail.publishing.neverPublished'))).length).toBe(3)
    expect(
      screen.queryByRole('button', { name: i18n.t('stockDetail.publishing.publishTo', { channel: 'AutoScout24' }) }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockDetail.publishing.listingTextTitle'))).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockDetail.publishing.media.title'))).not.toBeInTheDocument()
  })

  it('offers publishing, listing texts and photos to a role that may publish', async () => {
    install(INVENTORY)
    render('publishing')

    expect(
      await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.publishTo', { channel: 'AutoScout24' }) }),
    ).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.publishing.listingTextTitle'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.publishing.media.title'))).toBeInTheDocument()
  })
})
