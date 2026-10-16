// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { SalesDealRead, StockItemPage, StockItemRead, ValuationRead } from '../api/types'
import { StockListPage } from './StockListPage'
import { SalesListPage } from './SalesListPage'
import { ValuationsListPage } from './ValuationsListPage'
import { CatalogueBrowsePage } from './CatalogueBrowsePage'

// KAN-361 — Stock, Sales, Valuations and Catalogue passed
// `gridPrefs.columnLayout ?? undefined` to DataGrid, so a user who never
// opened the Columns panel saw every column (about 60 on Stock), each
// column's `defaultVisible: false` ignored. They now pass
// `defaultColumnLayout(columnRegistry)` when no layout is saved, to the grid
// and to the panel alike (ADR-060: a documented subset visible by default,
// the rest one click away).
//
// KAN-362 — the panel spoke English in every UI language: no grid passed
// its labels, and the kit hardcoded the pin and move buttons' names.

// jsdom has no layout: render every row (the shim the columns tests use).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const NOW = '2026-10-01T09:00:00Z'
const STOCK_ITEM: StockItemRead = {
  id: 's1', stockNumber: 'ST-3610', vehicleLabel: 'VW Golf GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: null,
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  odometerKm: 12500, listPrice: '42500.00', effectivePrice: '41900.50', basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null,
  expectedDelivery: null, pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null,
  purchasePrice: null, supplierIsVatRegistered: null, supplierName: null, valuationRefAmount: null,
  valuationRefId: null, valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW,
  version: 1,
}
const DEAL = {
  id: 'd1', entityType: 'offer', number: 'O-000361', status: 'draft', offerId: 'o1', offerNumber: 'O-000361',
  contractId: null, contractNumber: null, customerId: null, customerLabel: 'Muster AG', customerLocality: 'Bern',
  vehicleLabel: 'VW Golf GTI', grossPrice: '42500.00', margin: null, documentsCount: 0, createdAt: NOW, updatedAt: NOW,
} as unknown as SalesDealRead
const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000361', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: null, customerLabel: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null,
  vehicleVin: null, finalOffer: '12000.00', deductions: [], version: 1, validFrom: '2026-10-01',
  validUntil: '2026-10-31', createdAt: NOW, updatedAt: NOW,
} as unknown as ValuationRead
const VARIANT = {
  id: 'v1', brandId: 'brand-vw', brandDisplayName: 'Volkswagen', modelGroupId: 'group-golf', modelGroupName: 'Golf',
  variantName: 'Golf GTI', modelYearFrom: 2021, modelYearTo: null, inProduction: true, vehicleKind: 'passenger_car',
  fuelType: 'petrol', bodyStyle: 'hatchback', drivetrain: 'fwd', transmission: 'automatic', typeApprovalNumbers: [],
  currentPrice: { amount: '42500.00', year: 2021, isNet: false },
  spec: { ps: 245, displacementCcm: 1984, doors: 5, seats: 5, trimName: 'GTI' }, updatedAt: NOW,
}
const page = <T,>(items: T[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

const CATALOGUE_ROUTES: FakeRoute[] = [
  {
    method: 'GET',
    match: /\/vehicle-mdm\/brands$/,
    handler: () => ({ items: [{ id: 'brand-vw', code: 'vw', displayName: 'Volkswagen', version: 1, createdAt: '', updatedAt: '' }], nextCursor: null }),
  },
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  { method: 'GET', match: /\/catalogue\/variants$/, handler: () => ({ ...page([VARIANT]), browseAvailable: true }) },
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
]

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const exactly = (text: string) => new RegExp(`^${escape(text)}$`, 'i')
const headerTexts = () => screen.getAllByRole('columnheader').map((th) => th.textContent ?? '')

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
  return screen.findByRole('list')
}

describe('Grids with no saved layout show their default subset (KAN-361)', () => {
  it('Stock: a defaultVisible: false column (VIN) is not on screen, and the Columns panel shows it', async () => {
    const user = userEvent.setup()
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: (): StockItemPage => page([STOCK_ITEM]) }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('ST-3610')

    const vin = i18n.t('stockList.columns.vin')
    expect(headerTexts().some((text) => text.startsWith(vin))).toBe(false)
    // The vehicle cell carries the VIN as its second line; the VIN column is the second copy.
    const row = () => screen.getByRole('row', { name: /ST-3610/ })
    expect(within(row()).getAllByText('WVWZZZ1KZ00000001')).toHaveLength(1)

    const list = await openPanel(user)
    const vinBox = within(list).getByRole('checkbox', { name: exactly(vin) })
    expect(vinBox).not.toBeChecked()
    await user.click(vinBox)

    expect(headerTexts().some((text) => text.startsWith(vin))).toBe(true)
    expect(within(row()).getAllByText('WVWZZZ1KZ00000001')).toHaveLength(2)
  })

  it('Catalogue: a defaultVisible: false column (drivetrain) is not on screen, and the Columns panel shows it', async () => {
    const user = userEvent.setup()
    installFakeBackend(CATALOGUE_ROUTES)
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue' })
    await screen.findByText('Golf GTI')

    const drivetrain = i18n.t('catalogueBrowse.columns.drivetrain')
    expect(headerTexts().some((text) => text.startsWith(drivetrain))).toBe(false)

    const list = await openPanel(user)
    const box = within(list).getByRole('checkbox', { name: exactly(drivetrain) })
    expect(box).not.toBeChecked()
    await user.click(box)

    expect(headerTexts().some((text) => text.startsWith(drivetrain))).toBe(true)
  })

  // Sales and Valuations declare no defaultVisible: false column today, so
  // nothing is hidden by default; what can be checked is that the panel and
  // the grid read the same layout — every ticked column is on screen.
  it.each([
    ['Sales', () => <SalesListPage />, '/sales', { match: /^\/sales\/deals$/, handler: () => page([DEAL]) }, 'O-000361'],
    ['Valuations', () => <ValuationsListPage />, '/valuations', { match: /^\/valuations$/, handler: () => page([VALUATION]) }, 'B-000361'],
  ] as const)('%s: the panel ticks exactly the columns the grid shows', async (_name, ui, route, backendRoute, rowText) => {
    const user = userEvent.setup()
    installFakeBackend([backendRoute as FakeRoute])
    renderWithProviders(ui(), { route })
    await screen.findByText(rowText)

    const list = await openPanel(user)
    const boxes = within(list).getAllByRole('checkbox')
    expect(boxes.length).toBeGreaterThan(0)
    const headers = headerTexts()
    for (const box of boxes) {
      const label = box.closest('label')?.textContent ?? ''
      expect(headers.some((text) => text.startsWith(label)), label).toBe((box as HTMLInputElement).checked)
    }
  })
})

describe('The Columns panel speaks the UI language (KAN-362)', () => {
  it('French UI: trigger, search, reset, pin and move controls are French', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: (): StockItemPage => page([STOCK_ITEM]) }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('ST-3610')

    // Hardcoded expectations, not i18n.t(): a key missing on both sides must fail here.
    expect(screen.queryByRole('button', { name: 'Columns' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Colonnes' }))
    expect(screen.getByPlaceholderText('Rechercher une colonne…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Rétablir par défaut/ })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Épingler la colonne' }).length).toBeGreaterThan(0)
    const stockNumber = i18n.t('stockList.columns.stockNumber')
    expect(screen.getByRole('button', { name: `Descendre ${stockNumber}` })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Pin column$|^Move .* (up|down)$|Reset to default/ })).toBeNull()
    expect(screen.queryByPlaceholderText('Find a column…')).toBeNull()
  })
})
