// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { RecordCostDialog } from './stock-detail/RecordCostDialog'
import { PriceBuildUp } from './PriceBuildUp'
import { ValuationCreateDialog } from './ValuationCreateDialog'
import type { SalesPricedDealRead } from '../api/types'

// KAN-168 / ADR-076: a CHF input reads `CHF 12'500.00` on every screen, in
// every UI language — under the French UI too, whose own CLDR number format
// would be `12 500,00`. One test per screen family (stock, offer,
// valuation); each sends the plain number, as before.

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterAll(async () => {
  await i18n.changeLanguage('de')
})
afterEach(() => cleanup())

describe("CHF inputs under the French UI (KAN-168)", () => {
  it("stock — a Wagenbuch cost typed as 12500 reads CHF 12'500.00 and is sent as 12500", async () => {
    installFakeBackend([])
    const user = userEvent.setup()
    const onSubmit = vi.fn(async () => {})
    renderWithProviders(<RecordCostDialog opened onClose={() => {}} onSubmit={onSubmit} />)

    await user.click(screen.getByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.category') }))
    await user.click((await screen.findAllByRole('option'))[0])
    const amount = screen.getByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.amount') }) as HTMLInputElement
    await user.type(amount, '12500')
    await user.tab()

    expect(amount.value).toBe("CHF 12'500.00")
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.wagenbuch.submit') }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ amount: 12500 }))
  })

  it("offer — a saved base price and amount discount open as CHF 42'500.00 and CHF 500.00", async () => {
    installFakeBackend([])
    const deal = {
      id: 'offer-1',
      vehicleSource: 'manual',
      vehicleSnapshotFrozenAt: null,
      manualBasePrice: '42500.00',
      listPrice: '42500.00',
      discountType: 'amount',
      discountValue: '500.00',
    } as unknown as SalesPricedDealRead
    renderWithProviders(<PriceBuildUp deal={deal} onDiscountChange={() => {}} onManualBasePriceChange={() => {}} />)

    const basePrice = screen.getByRole('textbox', { name: i18n.t('offerWorkspace.pricing.manualBasePrice') }) as HTMLInputElement
    const discount = screen.getByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') }) as HTMLInputElement
    expect(basePrice.value).toBe("CHF 42'500.00")
    expect(discount.value).toBe('CHF 500.00')
  })

  it('offer — a percentage discount is not a CHF amount and shows no CHF', async () => {
    installFakeBackend([])
    const deal = {
      id: 'offer-2',
      vehicleSource: 'manual',
      vehicleSnapshotFrozenAt: null,
      manualBasePrice: null,
      discountType: 'percent',
      discountValue: '5',
    } as unknown as SalesPricedDealRead
    renderWithProviders(<PriceBuildUp deal={deal} onDiscountChange={() => {}} />)

    const discount = screen.getByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') }) as HTMLInputElement
    expect(discount.value).toBe('5 %')
  })

  it("valuation — a final offer typed as 12500 reads CHF 12'500.00 and is sent as \"12500\"", async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
      { method: 'POST', match: /^\/valuations$/, handler: () => ({ __status: 201, body: { id: 'val-1' } }) },
    ])
    const user = userEvent.setup()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    const finalOffer = within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false }) as HTMLInputElement
    await user.type(finalOffer, '12500')
    await user.tab()

    expect(finalOffer.value).toBe("CHF 12'500.00")
    await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))
    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/valuations$/, 'POST')[0].body).toMatchObject({ finalOffer: '12500' })
  })
})
