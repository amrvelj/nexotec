// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'

// KAN-307 — on a car in stock, "Offerte erstellen" and "Vertrag erstellen"
// start a deal for that car and open it (Anto, 2026-10-12: the contract
// works like the offer). Before, both were disabled for every car with
// "Verkauf ist noch nicht verfügbar." A car that has left stock keeps
// KAN-262's disabled-with-reason (StockDetailPage.leftStock.render.test.tsx).

const ID = 'st-in-stock'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000009', vehicleLabel: 'VW Golf 2019', vin: null, vehicleId: null, condition: 'used',
  lifecycleStatus: 'in_stock', reservationState: 'none', bodyStyle: null, exteriorColour: null, odometerKm: null,
  firstRegistrationDate: null, basePrice: null, listPrice: null, effectivePrice: null, landedCost: null,
  notionalInputTaxApplicable: null, notionalInputTaxRate: null, notionalInputTaxAmount: null,
  notionalInputTaxOverridden: false, purchasePrice: null, purchaseDate: null, purchaseInvoiceRef: null,
  supplierName: null, supplierIsVatRegistered: null, orderDate: null, expectedDelivery: null, inStockAt: null,
  leftStockAt: null, pipelineRef: null, locationId: null, valuationRefId: null, valuationRefSource: null,
  valuationRefAmount: null, valuationRefValuedAt: null, isInvoiceable: true, version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

function install({ failContract = false }: { failContract?: boolean } = {}) {
  return installFakeBackend([
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => ITEM },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
    { method: 'POST', match: /^\/sales\/offers$/, handler: () => ({ __status: 201, body: { id: 'of-1', version: 1 } }) },
    { method: 'PATCH', match: /^\/sales\/offers\/of-1$/, handler: () => ({ id: 'of-1', version: 2 }) },
    {
      method: 'POST',
      match: /^\/sales\/contracts$/,
      handler: () =>
        failContract
          ? status(503, { error: { code: 'unavailable', message: 'Sales unavailable', details: null } })
          : { __status: 201, body: { id: 'ct-1', version: 2 } },
    },
  ])
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/stock/:id" element={<StockDetailPage />} />
      <Route path="/sales/offers/:id" element={<p>offer workspace</p>} />
      <Route path="/sales/contracts/:id" element={<p>contract workspace</p>} />
    </Routes>,
    { route: `/stock/${ID}` },
  )
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('StockDetailPage — sales actions on a car in stock (KAN-307)', () => {
  it('"Offerte erstellen" creates an offer with this car and opens it', async () => {
    const user = userEvent.setup()
    const backend = install()
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.actions.createOffer') }))

    expect(await screen.findByText('offer workspace')).toBeInTheDocument()
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
    const [patch] = backend.callsTo(/^\/sales\/offers\/of-1$/, 'PATCH')
    expect(patch.body).toEqual({ vehicleSource: 'stock', stockItemId: ID, vehicleLabel: 'VW Golf 2019' })
    expect(patch.headers.get('If-Match')).toBe('1')
  })

  it('"Vertrag erstellen" creates a contract with this car and opens it', async () => {
    const user = userEvent.setup()
    const backend = install()
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.actions.createContract') }))

    expect(await screen.findByText('contract workspace')).toBeInTheDocument()
    const [post] = backend.callsTo(/^\/sales\/contracts$/, 'POST')
    expect(post.body).toEqual({ stockItemId: ID, vehicleLabel: 'VW Golf 2019' })
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a failed creation is said, not dropped, and the actions stay usable', async () => {
    const user = userEvent.setup()
    install({ failContract: true })
    render()

    const contract = await screen.findByRole('button', { name: i18n.t('stockDetail.actions.createContract') })
    await user.click(contract)

    expect(await screen.findByText('Sales unavailable')).toBeInTheDocument()
    expect(screen.queryByText('contract workspace')).not.toBeInTheDocument()
    expect(contract).toBeEnabled()
  })
})
