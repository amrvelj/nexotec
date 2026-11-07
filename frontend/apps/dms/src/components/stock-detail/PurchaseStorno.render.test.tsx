// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { testTheme } from '../../test/testTheme'
import i18n from '../../i18n'
import { installFakeBackend, status } from '../../test/fakeBackend'
import { TranslatedOverlayProvider } from '../../test/TranslatedOverlayProvider'
import { DetailsTab } from './DetailsTab'
import { WagenbuchTab } from './WagenbuchTab'
import type { LedgerEntryRead, StockItemRead } from '../../api/types'

// KAN-146 (PRD-Stock FR-I-05; Anto, 2026-11-05) — «Einkauf stornieren» on
// the stock detail's purchase card: the role that books the purchase reverses
// it with a reason, the refusal is said in the UI language, a car that left
// stock keeps the action visible but disabled with the reason, and a stornoed
// car (back in pipeline with its VIN) is bought again without being asked for
// a VIN.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const NOW = '2026-10-01T09:00:00Z'
const BOUGHT: StockItemRead = {
  id: 's1', stockNumber: 'ST-1001', vehicleLabel: 'VW Golf GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: 'v1',
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'reserved', ageingBucket: 'green',
  odometerKm: 12500, listPrice: '42500.00', effectivePrice: '41900.50', basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: false,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
  pipelineRef: null, purchaseDate: '2026-09-15', purchaseInvoiceRef: null, purchasePrice: '20000.00',
  supplierIsVatRegistered: true, supplierName: 'Auto Grossist AG', valuationRefAmount: null, valuationRefId: null,
  valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 4,
}
const STORNOED: StockItemRead = {
  ...BOUGHT, lifecycleStatus: 'pipeline', inStockAt: null, isInvoiceable: false, purchaseDate: null,
  purchasePrice: null, supplierIsVatRegistered: null, supplierName: null, notionalInputTaxApplicable: null, version: 5,
}

function renderTab(item: StockItemRead, props: Partial<Parameters<typeof DetailsTab>[0]> = {}) {
  return render(
    <MantineProvider theme={testTheme} env="test">
      <QueryClientProvider client={new QueryClient()}>
        <TranslatedOverlayProvider>
          <DetailsTab item={item} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} {...props} />
        </TranslatedOverlayProvider>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

async function openPurchaseBlock(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText(i18n.t('stockDetail.purchase.title')))
}

describe('«Einkauf stornieren» (KAN-146)', () => {
  it('reverses the purchase with a reason, sent with If-Match and an Idempotency-Key', async () => {
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/inventory\/stock-items\/s1\/purchase\/storno$/, handler: () => STORNOED },
    ])
    const onPurchaseStornoed = vi.fn()
    const user = userEvent.setup()
    renderTab(BOUGHT, { onPurchaseStornoed })
    await openPurchaseBlock(user)

    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('ST-1001')

    // A blank reason is refused before the round trip.
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('stockDetail.purchase.storno.submit') }))
    expect(await screen.findByText(i18n.t('stockDetail.purchase.storno.reasonRequired'))).toBeInTheDocument()
    expect(backend.callsTo(/purchase\/storno$/)).toHaveLength(0)

    await user.type(
      screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.storno.reasonLabel')) }),
      '  Lieferant storniert  ',
    )
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('stockDetail.purchase.storno.submit') }))

    await waitFor(() => expect(onPurchaseStornoed).toHaveBeenCalledWith(STORNOED))
    const [call] = backend.callsTo(/purchase\/storno$/, 'POST')
    expect(call.body).toEqual({ reason: 'Lieferant storniert' })
    expect(call.headers.get('If-Match')).toBe('4')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('says a refusal in the UI language, naming the contract whose invoice was requested', async () => {
    await i18n.changeLanguage('it')
    installFakeBackend([
      {
        method: 'POST',
        match: /\/purchase\/storno$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'Contract K-0042 has requested the invoice for stock item ST-1001.',
              details: { reason: 'purchase_storno_invoice_requested', contractNumber: 'K-0042', stockItemId: 's1' },
            },
          }),
      },
    ])
    const user = userEvent.setup()
    renderTab(BOUGHT, { onPurchaseStornoed: vi.fn() })
    await openPurchaseBlock(user)
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') }))
    await user.type(
      screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.storno.reasonLabel')) }),
      'Errore',
    )
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('stockDetail.purchase.storno.submit') }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(
      i18n.t('apiRefusal.reasons.purchase_storno_invoice_requested', { contractNumber: 'K-0042' }),
    )
    expect(alert.textContent).toContain('K-0042')
    expect(screen.queryByText(/has requested the invoice/)).not.toBeInTheDocument()
  })

  it('stays visible but disabled, with the reason, on a car that has left stock', async () => {
    const user = userEvent.setup()
    renderTab({ ...BOUGHT, leftStockAt: NOW }, { onPurchaseStornoed: vi.fn() })
    await openPurchaseBlock(user)

    const button = screen.getByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(i18n.t('stockDetail.purchase.storno.leftStock'))
  })

  it('is not offered without a booked purchase, nor to a role that may not book one', async () => {
    const user = userEvent.setup()
    const { unmount } = renderTab(STORNOED, { onPurchaseStornoed: vi.fn() })
    await openPurchaseBlock(user)
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') })).not.toBeInTheDocument()
    unmount()

    renderTab(BOUGHT, { onPurchaseStornoed: vi.fn(), canRecordPurchase: false })
    await openPurchaseBlock(user)
    expect(screen.queryByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') })).not.toBeInTheDocument()
  })

  it('books a stornoed car again without asking for a VIN: it kept its own', async () => {
    installFakeBackend([])
    const user = userEvent.setup()
    renderTab(STORNOED, { onPurchaseStornoed: vi.fn() })
    await openPurchaseBlock(user)

    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.recordButton') }))
    await screen.findByRole('dialog')

    expect(
      screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.fields.vin')) }),
    ).not.toBeInTheDocument()
  })
})

// KAN-572 (Anto, 2026-11-07, option C) — on a trade-in's car the user says
// whether it went back to the customer who traded it in; that customer is then
// their owner and keeper again. Any other car is never asked.
describe('«Einkauf stornieren» on a trade-in (KAN-572)', () => {
  const TRADE_IN: StockItemRead = { ...BOUGHT, pipelineRef: 'contract:c-0042:trade_in' }
  const returnedLabel = () => i18n.t('stockDetail.purchase.storno.returnedToCustomer')

  async function openStorno(item: StockItemRead) {
    const user = userEvent.setup()
    renderTab(item, { onPurchaseStornoed: vi.fn() })
    await openPurchaseBlock(user)
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.storno.button') }))
    await screen.findByRole('dialog')
    await user.type(
      screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.storno.reasonLabel')) }),
      'Kunde behält das Fahrzeug',
    )
    return user
  }

  it('asks, unticked by default, and sends returnedToCustomer when ticked', async () => {
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/inventory\/stock-items\/s1\/purchase\/storno$/, handler: () => STORNOED },
    ])
    const user = await openStorno(TRADE_IN)

    const box = screen.getByRole('checkbox', { name: (n) => n.startsWith(returnedLabel()) })
    expect(box).not.toBeChecked()
    expect(box).toHaveAccessibleDescription(i18n.t('stockDetail.purchase.storno.returnedToCustomerHint'))
    await user.click(box)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('stockDetail.purchase.storno.submit') }))

    await waitFor(() => expect(backend.callsTo(/purchase\/storno$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/purchase\/storno$/, 'POST')[0].body).toEqual({
      reason: 'Kunde behält das Fahrzeug',
      returnedToCustomer: true,
    })
  })

  it('left unticked, says the car stayed: the customer keeps their history', async () => {
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/inventory\/stock-items\/s1\/purchase\/storno$/, handler: () => STORNOED },
    ])
    const user = await openStorno(TRADE_IN)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('stockDetail.purchase.storno.submit') }))

    await waitFor(() => expect(backend.callsTo(/purchase\/storno$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/purchase\/storno$/, 'POST')[0].body).toEqual({
      reason: 'Kunde behält das Fahrzeug',
      returnedToCustomer: false,
    })
  })

  it('is never asked for a car that is not a trade-in', async () => {
    installFakeBackend([])
    await openStorno(BOUGHT)
    expect(screen.queryByRole('checkbox', { name: (n) => n.startsWith(returnedLabel()) })).not.toBeInTheDocument()
  })
})

// KAN-146 — the storno's counter-entry carries the negated Einstandspreis.
// The Wagenbuch put its own «−» before every cost, so it read «−-CHF 24'500.00».
describe('Wagenbuch — a counter-entry reads as one signed amount (KAN-146)', () => {
  it('shows the purchase as −CHF and its storno as +CHF, netting to CHF 0.00', () => {
    const entry = (id: string, amount: string): LedgerEntryRead => ({
      id, stockItemId: 's1', category: 'einstandspreis', direction: 'cost', amount,
      occurredAt: NOW, sourceRef: `purchase:${id}`, isAuto: true, createdAt: NOW,
    })
    render(
      <MantineProvider theme={testTheme} env="test">
        <WagenbuchTab entries={[entry('e2', '-24500.00'), entry('e1', '24500.00')]} locale="de-CH" onRecordCost={vi.fn()} />
      </MantineProvider>,
    )

    const cells = screen.getAllByRole('cell').map((c) => c.textContent ?? '')
    expect(cells).toContain("+CHF 24'500.00")
    expect(cells).toContain("−CHF 24'500.00")
    expect(cells.some((text) => /[−-]\s*-/.test(text))).toBe(false)
    expect(screen.getByText('CHF 0.00')).toBeInTheDocument()
  })
})
