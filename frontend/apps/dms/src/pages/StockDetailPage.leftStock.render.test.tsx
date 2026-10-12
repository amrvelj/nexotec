// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemRead } from '../api/types'
import { StockDetailPage } from './StockDetailPage'

// KAN-262 — a car that has left stock (FR-I-12) keeps lifecycleStatus
// `in_stock` (ADR-054), so the page must read `leftStockAt`: it says when
// the car left instead of "An Lager" / "Frei", and offers neither an offer
// nor a contract (both disabled, with that as the reason — ADR-061).

const ID = 'st-sold'
const ITEM: StockItemRead = {
  id: ID, stockNumber: 'S-000007', vehicleLabel: 'Skoda Octavia 2020', vin: 'TMBJJ7NE5L0123456', vehicleId: null,
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', bodyStyle: null, exteriorColour: null,
  odometerKm: null, firstRegistrationDate: null, basePrice: null, listPrice: null, effectivePrice: null, landedCost: null,
  notionalInputTaxApplicable: null, notionalInputTaxRate: null, notionalInputTaxAmount: null,
  notionalInputTaxOverridden: false, purchasePrice: null, purchaseDate: null, purchaseInvoiceRef: null,
  supplierName: null, supplierIsVatRegistered: null, orderDate: null, expectedDelivery: null,
  inStockAt: '2026-03-02T10:00:00Z', leftStockAt: '2026-10-08T12:00:00Z', pipelineRef: null, locationId: null,
  valuationRefId: null, valuationRefSource: null, valuationRefAmount: null, valuationRefValuedAt: null,
  isInvoiceable: true, version: 4, createdAt: '2026-03-01T00:00:00Z', updatedAt: '2026-10-08T12:00:00Z',
}

function renderItem(item: StockItemRead) {
  installFakeBackend([
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}$`), handler: () => item },
    { method: 'GET', match: new RegExp(`^/inventory/stock-items/${ID}/ledger-entries$`), handler: () => ({ items: [] }) },
  ])
  renderWithProviders(
    <Routes>
      <Route path="/stock/:id" element={<StockDetailPage />} />
    </Routes>,
    { route: `/stock/${ID}` },
  )
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

const LEFT_STOCK = {
  de: { badge: 'Bestand verlassen am 08.10.2026', reason: 'Dieses Fahrzeug hat den Bestand am 08.10.2026 verlassen' },
  fr: { badge: 'A quitté le stock le 08.10.2026', reason: 'Ce véhicule a quitté le stock le 08.10.2026' },
  it: { badge: 'Uscito dallo stock il 08.10.2026', reason: 'Questo veicolo è uscito dallo stock il 08.10.2026' },
  en: { badge: 'Left stock on 08.10.2026', reason: 'This vehicle left stock on 08.10.2026' },
} as const

describe('StockDetailPage — a car that has left stock (KAN-262)', () => {
  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`says when it left stock instead of the lifecycle and reservation badges (${lng})`, async () => {
      await i18n.changeLanguage(lng)
      renderItem(ITEM)

      expect(await screen.findByText(LEFT_STOCK[lng].badge)).toBeInTheDocument()
      expect(screen.queryByText(i18n.t('stockEnums.lifecycleStatus.in_stock'))).not.toBeInTheDocument()
      expect(screen.queryByText(i18n.t('stockEnums.reservationState.none'))).not.toBeInTheDocument()
    })
  }

  it('offers neither an offer nor a contract, and says why', async () => {
    const user = userEvent.setup()
    renderItem(ITEM)

    const offer = await screen.findByRole('button', { name: i18n.t('stockDetail.actions.createOffer') })
    const contract = screen.getByRole('button', { name: i18n.t('stockDetail.actions.createContract') })
    expect(offer).toBeDisabled()
    expect(contract).toBeDisabled()

    await user.hover(contract.parentElement as HTMLElement)
    expect(await screen.findByText(new RegExp(LEFT_STOCK.de.reason))).toBeInTheDocument()
  })

  it('a car still in stock keeps its lifecycle and reservation badges', async () => {
    renderItem({ ...ITEM, leftStockAt: null })

    expect(await screen.findByText(i18n.t('stockEnums.lifecycleStatus.in_stock'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockEnums.reservationState.none'))).toBeInTheDocument()
    expect(screen.queryByText(/Bestand verlassen am/)).not.toBeInTheDocument()
  })
})
