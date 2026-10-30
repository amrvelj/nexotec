// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen } from '@testing-library/react'
import i18n from '../../i18n'
import { installFakeBackend } from '../../test/fakeBackend'
import { renderWithProviders } from '../../test/renderWithProviders'
import { ApiError } from '../../api/client'
import { BlockingConditionBanner } from './BlockingConditionBanner'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'

// KAN-358 — the purchase date drives the Wagenbuch and the fiktiver
// Vorsteuerabzug booking date. It was pre-filled with the UTC day, so a
// purchase recorded at 00:30 Swiss time proposed yesterday.

const ORIGINAL_TZ = process.env.TZ

beforeEach(() => {
  process.env.TZ = 'Europe/Zurich'
  vi.useFakeTimers({ toFake: ['Date'] })
  installFakeBackend([])
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  if (ORIGINAL_TZ === undefined) delete process.env.TZ
  else process.env.TZ = ORIGINAL_TZ
})

function purchaseDateField(): HTMLInputElement {
  return screen.getByLabelText(i18n.t('stockDetail.purchase.fields.purchaseDate'), { exact: false }) as HTMLInputElement
}

describe('RecordPurchaseDialog — the default purchase date (KAN-358)', () => {
  it('is the Swiss calendar day at 00:30 in summer, not the UTC day before', () => {
    // 2026-10-14T22:30Z is 00:30 on 15 October in Zurich (CEST, UTC+2).
    vi.setSystemTime(new Date('2026-10-14T22:30:00Z'))
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={async () => {}} />)

    expect(purchaseDateField().value).toBe('2026-10-15')
  })

  it('is the Swiss calendar day at 00:30 in winter', () => {
    // 2026-12-31T23:30Z is 00:30 on 1 January 2027 in Zurich (CET, UTC+1).
    vi.setSystemTime(new Date('2026-12-31T23:30:00Z'))
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={async () => {}} />)

    expect(purchaseDateField().value).toBe('2027-01-01')
  })
})

// KAN-437 — since KAN-376 a sales user reaches a stock item's detail and can
// open «Einkauf erfassen», which their role may not write (KAN-430). The 403
// is said in the UI language, never as the API's English text.
describe('RecordPurchaseDialog — a refusal in the UI language (KAN-437)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('says a refused purchase in Italian, never in the API\'s English text', async () => {
    await i18n.changeLanguage('it')
    const refused = new ApiError(403, 'forbidden', "Not permitted to write 'stock_acquisition'.", null)
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={() => Promise.reject(refused)} />)

    const field = (key: string) => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t(key)) })
    fireEvent.change(field('stockDetail.purchase.fields.supplierName'), { target: { value: 'Garage Muster AG' } })
    fireEvent.change(field('stockDetail.purchase.fields.purchasePrice'), { target: { value: '18000' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') }))

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('apiRefusal.forbidden'))
    expect(screen.queryByText(/Not permitted/)).not.toBeInTheDocument()
  })
})

// KAN-446 (ADR-045, FR-V-04) — a pipeline item is bought with its VIN: the
// dialog asks for it, holds the booking back without it, sends it, and says a
// refused VIN in the UI language.
describe('RecordPurchaseDialog — a pipeline item is bought with its VIN (KAN-446)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  const field = (key: string) => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t(key)) })
  const fillPurchase = () => {
    fireEvent.change(field('stockDetail.purchase.fields.supplierName'), { target: { value: 'Hans Muster' } })
    fireEvent.change(field('stockDetail.purchase.fields.purchasePrice'), { target: { value: '8000' } })
  }
  const submitButton = () => screen.getByRole('button', { name: i18n.t('stockDetail.purchase.submit') })

  it('asks for no VIN for an item already in stock', () => {
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={async () => {}} />)

    expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.purchase.fields.vin')) })).toBeNull()
  })

  it('holds the booking back until the VIN is entered, then sends it', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(<RecordPurchaseDialog opened requiresVin onClose={() => {}} onSubmit={onSubmit} />)

    fillPurchase()
    expect(submitButton()).toBeDisabled()

    fireEvent.change(field('stockDetail.purchase.fields.vin'), { target: { value: ' WVWZZZ1KZAW004461 ' } })
    expect(submitButton()).toBeEnabled()
    fireEvent.click(submitButton())

    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ supplierName: 'Hans Muster', purchasePrice: 8000, vin: 'WVWZZZ1KZAW004461' })
  })

  it.each([
    ['purchase_requires_vin', {}, 'apiRefusal.reasons.purchase_requires_vin', {}],
    ['vin_already_in_stock', { stockNumber: 'S-000042' }, 'apiRefusal.reasons.vin_already_in_stock', { stockNumber: 'S-000042' }],
    ['invalid_vin', {}, 'common.invalidVin', {}],
  ])('says a %s refusal in French', async (reason, extra, key, params) => {
    await i18n.changeLanguage('fr')
    const status = reason === 'vin_already_in_stock' ? 409 : 422
    const refused = new ApiError(status, 'refused', 'English text for logs only.', { reason, ...extra })
    renderWithProviders(<RecordPurchaseDialog opened requiresVin onClose={() => {}} onSubmit={() => Promise.reject(refused)} />)

    fillPurchase()
    fireEvent.change(field('stockDetail.purchase.fields.vin'), { target: { value: 'WVWZZZ1KZAW004461' } })
    fireEvent.click(submitButton())

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t(key, params))
    expect(screen.queryByText(/English text/)).not.toBeInTheDocument()
  })
})

// KAN-437 — a blocking condition's sentence comes from the API in German
// only; the banner says it from the marketplace field, in the UI language.
describe('BlockingConditionBanner — in the UI language (KAN-437)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('says each condition in French, keeping the marketplace field name, never the API\'s German sentence', async () => {
    await i18n.changeLanguage('fr')
    renderWithProviders(
      <BlockingConditionBanner
        conditions={[
          { field: 'Preis', message: 'Kein Effektivpreis hinterlegt.' },
          { field: 'NeuesFeld', message: 'Etwas fehlt.' },
        ]}
      />,
    )

    expect(screen.getByText('Preis')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.publishing.blockingCondition.Preis'), { exact: false })).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.publishing.blockingCondition.other'), { exact: false })).toBeInTheDocument()
    expect(screen.queryByText(/Kein Effektivpreis/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Etwas fehlt/)).not.toBeInTheDocument()
  })
})
