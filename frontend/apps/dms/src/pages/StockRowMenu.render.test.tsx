// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { useAuth } from '../auth/AuthContext'
import { ALL_ENTITLEMENTS, installFakeBackend, type FakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { StockItemPage, StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'
import { StockListPage } from './StockListPage'

// KAN-571 (ADR-061; PRD-Stock FR-I-07: the detail header carries "the same
// action set as the stock overview row menu") — the Stock list's ⋯ menu and
// the stock detail's overflow render from one builder, same items in the
// same order. An action picked in the list opens the car at that action.

// jsdom has no layout: render every row (as the other list render tests do).
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

const ID = 'st-571'
const NOW = '2026-11-07T09:00:00Z'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000571', vehicleLabel: 'Škoda Octavia Combi 2.0 TDI RS 4x4', vin: 'TMBJJ7NX5MY012345',
  vehicleId: 'v-1', condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  bodyStyle: null, exteriorColour: null, odometerKm: 23000, firstRegistrationDate: null, basePrice: null,
  listPrice: null, effectivePrice: null, landedCost: null, notionalInputTaxApplicable: false,
  notionalInputTaxRate: null, notionalInputTaxAmount: null, notionalInputTaxOverridden: false,
  purchasePrice: '20000.00', purchaseDate: '2026-10-20', purchaseInvoiceRef: null, supplierName: 'Privat',
  supplierIsVatRegistered: false, orderDate: null, expectedDelivery: null, inStockAt: NOW, leftStockAt: null,
  pipelineRef: null, locationId: null, valuationRefId: null, valuationRefSource: null, valuationRefAmount: null,
  valuationRefValuedAt: null, isInvoiceable: true, version: 3, configurationId: null, configurationLabel: null,
  createdAt: NOW, updatedAt: NOW,
}

function session(writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Ivo', lastName: 'Lager', email: 'ivo@example.ch', accessRoles: ['inventory'] },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function install(writableCapabilities?: string[], item: StockItemRead = ITEM): FakeBackend {
  return installFakeBackend([
    ...(writableCapabilities ? [session(writableCapabilities)] : []),
    { method: 'GET', match: /^\/inventory\/stock-items$/, handler: (): StockItemPage => ({ items: [item], nextCursor: null, total: 1, totalIsEstimate: false }) },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => item },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
    { method: 'POST', match: new RegExp(`^/inventory/stock-items/${ID}/condition$`), handler: () => ({ ...ITEM, condition: 'demo', version: 4 }) },
  ])
}

function DetailWithState() {
  const location = useLocation()
  return (
    <>
      <div data-testid="router-state">{JSON.stringify(location.state)}</div>
      <StockDetailPage />
    </>
  )
}

function render(route: string) {
  renderWithProviders(
    <Routes>
      <Route path="/stock" element={<StockListPage />} />
      <Route path="/stock/:id" element={<DetailWithState />} />
    </Routes>,
    { route },
  )
}

const names = (menu: HTMLElement) => within(menu).getAllByRole('menuitem').map((entry) => entry.textContent ?? '')

async function openListMenu(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('S-000571')
  await user.click(screen.getByRole('button', { name: i18n.t('common.rowActionsLabel') }))
  return screen.findByRole('menu')
}

afterEach(() => cleanup())

describe('Stock — one row menu for list and detail (KAN-571)', () => {
  it('the list lists «Öffnen», then exactly the detail overflow\'s items in the same order', async () => {
    const user = userEvent.setup()
    install()
    render('/stock')
    const listItems = names(await openListMenu(user))
    cleanup()

    install()
    render(`/stock/${ID}`)
    await screen.findAllByText('Škoda Octavia Combi 2.0 TDI RS 4x4')
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    const detailItems = names(await screen.findByRole('menu'))

    expect(listItems[0]).toContain(i18n.t('stockList.rowActions.open'))
    expect(listItems.slice(1)).toEqual(detailItems)
    const expected = [
      'stockDetail.purchase.recordButton',
      'stockDetail.rowMenu.changeCondition',
      'stockDetail.configuration.attach',
      'stockDetail.vinCorrection.action',
      'stockDetail.actions.createContract',
      'stockDetail.actions.createOffer',
      'stockDetail.purchase.storno.button',
    ].map((key) => i18n.t(key))
    expect(detailItems).toHaveLength(expected.length)
    expected.forEach((name, index) => expect(detailItems[index]).toContain(name))
  })

  it('«Einkauf erfassen» on a bought car is shown disabled, with its reason', async () => {
    const user = userEvent.setup()
    install()
    render('/stock')
    const menu = await openListMenu(user)

    const record = within(menu).getByRole('menuitem', { name: new RegExp(i18n.t('stockDetail.purchase.recordButton')) })
    expect(record).toHaveAttribute('data-disabled')
    expect(record.textContent).toContain(i18n.t('stockDetail.rowMenu.purchaseAlreadyBooked'))
  })

  it('«Zustand ändern» from the list opens the car with its dialog; saving sends the condition with If-Match', async () => {
    const user = userEvent.setup()
    const backend = install()
    render('/stock')
    const menu = await openListMenu(user)
    await user.click(within(menu).getByRole('menuitem', { name: new RegExp(i18n.t('stockDetail.rowMenu.changeCondition')) }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('stockDetail.condition.body', { stockNumber: 'S-000571' }))).toBeInTheDocument()
    // The list's action is used up: a reload or Back never runs it again.
    await waitFor(() => expect(screen.getByTestId('router-state').textContent).toBe('null'))

    const submit = within(dialog).getByRole('button', { name: i18n.t('stockDetail.condition.submit') })
    expect(submit).toBeDisabled() // the current condition, unchanged
    await user.click(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.condition.label')) }))
    await user.click(await screen.findByRole('option', { name: i18n.t('stockEnums.condition.demo') }))
    await user.click(submit)

    await waitFor(() => expect(backend.callsTo(/\/condition$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/condition$/, 'POST')
    expect(call.body).toEqual({ condition: 'demo' })
    expect(call.headers.get('If-Match')).toBe('3')
  })

  it('«Einkauf stornieren» from the list opens the car with the storno dialog', async () => {
    const user = userEvent.setup()
    install()
    render('/stock')
    const menu = await openListMenu(user)
    await user.click(within(menu).getByRole('menuitem', { name: new RegExp(i18n.t('stockDetail.purchase.storno.button')) }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: i18n.t('stockDetail.purchase.storno.title') })).toBeInTheDocument()
  })

  describe('«VIN korrigieren» (KAN-557)', () => {
    const label = () => i18n.t('stockDetail.vinCorrection.action')
    const entryIn = (menu: HTMLElement) => within(menu).queryByRole('menuitem', { name: (n) => n.includes(label()) })

    async function bothMenus(user: ReturnType<typeof userEvent.setup>, item: StockItemRead, capabilities?: string[]) {
      install(capabilities, item)
      render('/stock')
      const list = entryIn(await openListMenu(user))
      const listState = list ? { disabled: list.hasAttribute('data-disabled'), text: list.textContent ?? '' } : null
      cleanup()
      install(capabilities, item)
      render(`/stock/${ID}`)
      await screen.findAllByText('Škoda Octavia Combi 2.0 TDI RS 4x4')
      await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
      const detail = entryIn(await screen.findByRole('menu'))
      const detailState = detail ? { disabled: detail.hasAttribute('data-disabled'), text: detail.textContent ?? '' } : null
      return { list: listState, detail: detailState }
    }

    it('is in both menus on a bought car, enabled', async () => {
      const { list, detail } = await bothMenus(userEvent.setup(), ITEM)
      expect(list).toEqual({ disabled: false, text: expect.stringContaining(label()) })
      expect(detail).toEqual(list)
    })

    it('on a car that left stock: disabled in both, with the left-stock reason', async () => {
      const { list, detail } = await bothMenus(userEvent.setup(), { ...ITEM, leftStockAt: '2026-11-01T09:00:00Z' })
      const reason = i18n.t('stockDetail.actions.leftStock', { date: '01.11.2026' })
      expect(list?.disabled).toBe(true)
      expect(list?.text).toContain(reason)
      expect(detail).toEqual(list)
    })

    it('on a car without a vehicle-mdm record (never bought): disabled in both, saying the configuration corrects it', async () => {
      const pipeline: StockItemRead = { ...ITEM, vehicleId: null, purchasePrice: null, lifecycleStatus: 'pipeline', inStockAt: null }
      const { list, detail } = await bothMenus(userEvent.setup(), pipeline)
      expect(list?.disabled).toBe(true)
      expect(list?.text).toContain(i18n.t('stockDetail.vinCorrection.needsPurchase'))
      expect(detail).toEqual(list)
    })

    it('is hidden in both without the stock_vin_correction row (KAN-430)', async () => {
      const { list, detail } = await bothMenus(userEvent.setup(), ITEM, [
        'configurations', 'stock_items', 'stock_condition', 'stock_acquisition', 'sales_offers', 'sales_contracts',
      ])
      expect(list).toBeNull()
      expect(detail).toBeNull()
    })

    it('from the list opens the car with its dialog, naming the stock number', async () => {
      const user = userEvent.setup()
      install()
      render('/stock')
      await user.click(entryIn(await openListMenu(user))!)

      const dialog = await screen.findByRole('dialog')
      expect(within(dialog).getByText(i18n.t('stockDetail.vinCorrection.body', { stockNumber: 'S-000571' }))).toBeInTheDocument()
      await waitFor(() => expect(screen.getByTestId('router-state').textContent).toBe('null'))
    })
  })

  it('a role that may not create offers or contracts sees neither in the header nor in the menu (KAN-430)', async () => {
    const user = userEvent.setup()
    install(['configurations', 'stock_items', 'stock_condition', 'stock_acquisition', 'marketplace_publishing'])
    render(`/stock/${ID}`)
    await screen.findAllByText('Škoda Octavia Combi 2.0 TDI RS 4x4')

    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.actions.createContract') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.actions.createOffer') })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    const items = names(await screen.findByRole('menu'))
    expect(items.some((n) => n.includes(i18n.t('stockDetail.actions.createContract')))).toBe(false)
    expect(items.some((n) => n.includes(i18n.t('stockDetail.rowMenu.changeCondition')))).toBe(true)
  })

  it('a hand-made router state for an action the role may not use runs nothing', async () => {
    const backend = install(['configurations', 'stock_items', 'stock_condition', 'stock_acquisition'])
    renderWithProviders(
      <Routes>
        <Route path="/start" element={<Navigate to={`/stock/${ID}`} state={{ stockOpenAction: 'createContract' }} />} />
        <Route path="/stock/:id" element={<DetailWithState />} />
      </Routes>,
      { route: '/start' },
    )
    await screen.findAllByText('Škoda Octavia Combi 2.0 TDI RS 4x4')
    await waitFor(() => expect(screen.getByTestId('router-state').textContent).toBe('null'))
    expect(backend.callsTo(/^\/sales\/contracts$/, 'POST')).toHaveLength(0)
  })

  it('«Vertrag erstellen» asked for by the router state creates exactly one contract, under StrictMode', async () => {
    // StrictMode runs a mounting component's effects twice. The detail mounts
    // only once the session is read (as ProtectedRoute does in the app) and
    // the car is already cached, so the action is ready in the mount phase:
    // without the once-per-navigation guard the second run would POST a
    // second contract.
    const backend = installFakeBackend([
      { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => ITEM },
      { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
      { method: 'POST', match: /^\/sales\/contracts$/, handler: () => ({ id: 'k-1', version: 1 }) },
    ])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    queryClient.setQueryData(['stock-item', ID], ITEM)
    renderWithProviders(
      <Routes>
        <Route path="/start" element={<Navigate to={`/stock/${ID}`} state={{ stockOpenAction: 'createContract' }} />} />
        <Route path="/stock/:id" element={<AfterSession><DetailWithState /></AfterSession>} />
        <Route path="/sales/contracts/:id" element={<p>contract page</p>} />
      </Routes>,
      { route: '/start', strictMode: true, queryClient },
    )

    expect(await screen.findByText('contract page')).toBeInTheDocument()
    expect(backend.callsTo(/^\/sales\/contracts$/, 'POST')).toHaveLength(1)
  })
})

/** Renders its children once the session is read, as ProtectedRoute does. */
function AfterSession({ children }: { children: React.ReactNode }) {
  return useAuth().loading ? null : <>{children}</>
}
