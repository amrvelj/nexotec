// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { DetailsTab } from './DetailsTab'
import type { StockItemRead } from '../../api/types'

// KAN-160: the commercial card's inline-edit fields show the Swiss-formatted
// value (`12'500`, `CHF 42'500.00`) — the same figure the spec grid above
// shows — while the editor still opens on the raw value the API accepts.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const NOW = '2026-10-01T09:00:00Z'
const ITEM: StockItemRead = {
  id: 's1', stockNumber: 'ST-1001', vehicleLabel: 'VW Golf GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: null,
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  odometerKm: 12500, listPrice: '42500.00', effectivePrice: '41900.50', basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
  pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null, purchasePrice: null, supplierIsVatRegistered: null,
  supplierName: null, valuationRefAmount: null, valuationRefId: null, valuationRefSource: null,
  valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 1,
}

describe('DetailsTab commercial card', () => {
  it('shows the Swiss-formatted odometer and prices under the French UI, and edits the raw value', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    render(
      <MantineProvider theme={theme} env="test">
        <DetailsTab item={ITEM} locale="fr-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />
      </MantineProvider>,
    )

    expect(screen.getByText('12\'500')).toBeInTheDocument() // commercial card odometer
    expect(screen.getByText("12'500 km")).toBeInTheDocument() // spec grid
    expect(screen.getAllByText("CHF 42'500.00").length).toBeGreaterThan(0)
    expect(screen.getAllByText("CHF 41'900.50").length).toBeGreaterThan(0)

    await user.click(screen.getByText("12'500"))
    expect(screen.getByDisplayValue('12500')).toBeInTheDocument()
  })
})
