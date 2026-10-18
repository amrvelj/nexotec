// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
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

// KAN-333 — the first registration and purchase dates are date-only values;
// formatDate rendered them a day early anywhere west of Greenwich.
describe('DetailsTab — date-only fields keep their day west of Greenwich (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders the first registration and purchase dates as the recorded day', async () => {
    const user = userEvent.setup()
    render(
      <MantineProvider theme={theme} env="test">
        <DetailsTab
          item={{ ...ITEM, firstRegistrationDate: '2021-04-01', purchaseDate: '2026-09-15', purchasePrice: '20000.00' }}
          locale="de-CH"
          onSaveField={vi.fn()}
          onReload={vi.fn()}
          onRecordPurchase={vi.fn()}
        />
      </MantineProvider>,
    )

    expect(screen.getByText('01.04.2021')).toBeInTheDocument()
    // The purchase card sits behind the commercial-data disclosure.
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.title') }))
    expect(screen.getByText('15.09.2026')).toBeInTheDocument()
    expect(screen.queryByText('31.03.2021')).not.toBeInTheDocument()
    expect(screen.queryByText('14.09.2026')).not.toBeInTheDocument()
  })
})

// KAN-359 (Anto, option A) — a car with a list price but no selling price
// yet showed a 28-px "not set" headline; it now shows the list price,
// captioned as such, and the selling price once one is built.
describe('DetailsTab — the headline before a selling price is built (KAN-359)', () => {
  function renderItem(item: StockItemRead) {
    render(
      <MantineProvider theme={theme} env="test">
        <DetailsTab item={item} locale="fr-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />
      </MantineProvider>,
    )
  }

  it('shows the list price with its caption, never "not set", in fr', async () => {
    await i18n.changeLanguage('fr')
    renderItem({ ...ITEM, listPrice: '24900.00', effectivePrice: null })

    const caption = screen.getByText(i18n.t('stockDetail.headline.listPriceCaption'))
    const headline = caption.parentElement as HTMLElement
    expect(headline).toHaveTextContent("CHF 24'900.00")
    expect(headline).not.toHaveTextContent(i18n.t('common.notSet'))
  })

  it('shows the selling price without the caption once one is built', async () => {
    await i18n.changeLanguage('fr')
    renderItem(ITEM)

    expect(screen.queryByText(i18n.t('stockDetail.headline.listPriceCaption'))).toBeNull()
  })
})
