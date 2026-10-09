// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'

// KAN-266 — the stock detail page's POSTs (the purchase, a Wagenbuch cost)
// carry one Idempotency-Key per submission, and a successful write retires
// it. A retry after a failure keeping its key is pinned where a failed save
// can be rendered: RecordCostDialog.render.test.tsx. The purchase dialog
// does not catch a failed save yet (its own ticket).

const ID = 'st-1'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000002', vehicleLabel: 'VW Golf 2019', vin: null, vehicleId: null, condition: 'used',
  lifecycleStatus: 'in_stock', reservationState: 'none', bodyStyle: null, exteriorColour: null, odometerKm: null,
  firstRegistrationDate: null, basePrice: null, listPrice: null, effectivePrice: null, landedCost: null,
  notionalInputTaxApplicable: null, notionalInputTaxRate: null, notionalInputTaxAmount: null,
  notionalInputTaxOverridden: false, purchasePrice: null, purchaseDate: null, purchaseInvoiceRef: null,
  supplierName: null, supplierIsVatRegistered: null, orderDate: null, expectedDelivery: null, inStockAt: null,
  leftStockAt: null, pipelineRef: null, locationId: null, valuationRefId: null, valuationRefSource: null,
  valuationRefAmount: null, valuationRefValuedAt: null, isInvoiceable: true, version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

function install() {
  return installFakeBackend([
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => ITEM },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
    {
      method: 'POST',
      match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`),
      handler: (req) => ({ __status: 201, body: { id: 'le-1', stockItemId: ID, ...(req.body as object) } }),
    },
    {
      method: 'POST',
      match: new RegExp(`^/inventory/stock-items/${ID}/purchase$`),
      handler: (req) => ({ ...ITEM, ...(req.body as object), version: 2 }),
    },
  ])
}

function renderTab(tab: string) {
  renderWithProviders(
    <Routes>
      <Route path="/stock/:id" element={<StockDetailPage />} />
    </Routes>,
    { route: `/stock/${ID}?tab=${tab}` },
  )
}

const keysOf = (backend: ReturnType<typeof install>, segment: string) =>
  backend.callsTo(new RegExp(`/${segment}$`), 'POST').map((call) => call.headers.get('Idempotency-Key'))

afterEach(() => cleanup())

describe('StockDetailPage — one Idempotency-Key per submission (KAN-266)', () => {
  it('two identical costs booked in a row are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderTab('wagenbuch')

    const book = async () => {
      await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.wagenbuch.recordButton') }))
      await user.click(await screen.findByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.category') }))
      await user.click((await screen.findAllByRole('option'))[0])
      await user.type(screen.getByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.amount') }), '350')
      await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.wagenbuch.submit') }))
    }
    await book()
    await waitFor(() => expect(keysOf(backend, 'ledger-entries')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await book()
    await waitFor(() => expect(keysOf(backend, 'ledger-entries')).toHaveLength(2))

    const [first, second] = keysOf(backend, 'ledger-entries')
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('the purchase carries a key beside its If-Match', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderTab('details')

    await user.click(await screen.findByText(i18n.t('stockDetail.purchase.title')))
    await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') }))
    const dialog = await screen.findByRole('dialog')
    // The first match: the VAT-registered checkbox's label starts with the supplier's.
    const field = (key: string) =>
      within(dialog).getAllByLabelText(i18n.t(`stockDetail.purchase.fields.${key}`), { exact: false })[0]
    await user.type(field('supplierName'), 'Garage Muster AG')
    await user.type(field('purchasePrice'), '18500')
    fireEvent.change(field('purchaseDate'), { target: { value: '2026-09-01' } })
    await user.click(within(dialog).getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))
    await waitFor(() => expect(backend.callsTo(/\/purchase$/, 'POST')).toHaveLength(1))

    const [call] = backend.callsTo(/\/purchase$/, 'POST')
    expect(call.headers.get('If-Match')).toBe('1')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })
})
