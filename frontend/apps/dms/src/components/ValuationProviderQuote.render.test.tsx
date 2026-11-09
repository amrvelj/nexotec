// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { configurationRead } from '../test/configuratorFixtures'
import { ValuationCreateDialog } from './ValuationCreateDialog'
import { ProviderQuoteCard } from './ProviderQuoteCard'
import { formatCurrencyChf } from '../utils/format'
import type { ProviderQuoteRead } from '../api/types'

// KAN-552 — «Bewertung von auto-i-dat holen»: the advisor's click asks the
// provider (never the dialog's opening), the stored quote's trade-in price
// becomes «Wert auto-i-dat» with purchase and sale beside it, and the
// valuation is created on the quote. Without the `bewertung` option the
// button is not there; a refusal says «nicht im Vertrag».

const configuration = configurationRead({
  mode: 'record',
  vin: 'ZARFAMBN5L7552001',
  firstRegistrationDate: '2020-03-02',
  mileageKm: 41000,
})

function quoteRead(overrides: Partial<ProviderQuoteRead> = {}): ProviderQuoteRead {
  return {
    id: 'quote-1',
    configurationId: 'cfg-1',
    fzKey: 'FZ100001',
    modelYear: 2020,
    firstRegistration: '2020-03-02',
    mileage: 41000,
    exchangePrice: '18400.00',
    purchasePrice: '17100.00',
    salePrice: '21700.00',
    vehicleExchangePrice: '18100.00',
    optionsExchangePrice: '300.00',
    kilometreCorrectionExchange: '-350.00',
    kilometreExpected: 45000,
    exchangeRangeFrom: '17500.00',
    exchangeRangeTo: '19300.00',
    providerManual: false,
    screenMessages: [{ de: 'Testhinweis', fr: 'Note de test', it: 'Nota di prova', en: 'Test note' }],
    residualTableState: 'present',
    residualTable: [
      { durationMonths: 12, kmPerYear: 10000, totalPercent: '64.4', totalPrice: '18700.00' },
      { durationMonths: 24, kmPerYear: 10000, totalPercent: '58.1', totalPrice: '16800.00' },
      { durationMonths: 12, kmPerYear: 20000, totalPercent: '62.7', totalPrice: '18200.00' },
      { durationMonths: 24, kmPerYear: 20000, totalPercent: '55.6', totalPrice: '16100.00' },
    ],
    providerRequestRef: 'ref-1',
    fetchedAt: '2026-11-09T07:00:00Z',
    ...overrides,
  }
}

function install({ granted = true, quote = (): unknown => ({ __status: 201, body: quoteRead() }) } = {}) {
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted }) },
    { method: 'POST', match: /^\/valuations\/provider-quotes$/, handler: () => quote() },
    { method: 'POST', match: /^\/valuations$/, handler: () => ({ __status: 201, body: { id: 'val-1' } }) },
  ])
}

async function openDialog() {
  renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} configuration={configuration} />)
  return screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
}

async function submitWithOffer(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  const offer = within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false })
  await user.type(offer, '17000')
  await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))
}

afterEach(() => cleanup())

describe('ValuationCreateDialog — the auto-i-dat valuation (KAN-552)', () => {
  it('asks the provider only on the click, shows the three prices and creates the valuation on the quote', async () => {
    const user = userEvent.setup()
    const backend = install()
    const dialog = await openDialog()
    const button = await within(dialog).findByRole('button', { name: i18n.t('valuationCreate.quote.fetch') })
    expect(backend.callsTo(/^\/valuations\/provider-quotes$/, 'POST')).toHaveLength(0)

    await user.click(button)

    const card = await within(dialog).findByTestId('provider-quote')
    expect(within(card).getByText(i18n.t('valuationQuote.exchange'))).toBeInTheDocument()
    expect(within(card).getByText(formatCurrencyChf(18400))).toBeInTheDocument()
    expect(within(card).getByText(formatCurrencyChf(17100))).toBeInTheDocument()
    expect(within(card).getByText(formatCurrencyChf(21700))).toBeInTheDocument()
    expect(within(card).getByText('Testhinweis')).toBeInTheDocument()
    expect(within(card).getByRole('table', { name: i18n.t('valuationQuote.residualTitle') })).toBeInTheDocument()
    const [request] = backend.callsTo(/^\/valuations\/provider-quotes$/, 'POST')
    expect(request.body).toEqual({ configurationId: 'cfg-1', firstRegistration: '2020-03-02', mileage: 41000 })
    expect(within(dialog).getByText(i18n.t('valuationSource.auto_i_dat'))).toBeInTheDocument()

    await submitWithOffer(user, dialog)

    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    const body = backend.callsTo(/^\/valuations$/, 'POST')[0].body as Record<string, unknown>
    expect(body.providerQuoteId).toBe('quote-1')
    expect(body.providerValue).toBe('18400')
    expect(body).not.toHaveProperty('source')
  })

  it('has no button without the bewertung option', async () => {
    install({ granted: false })
    const dialog = await openDialog()
    expect(await within(dialog).findByText(i18n.t('valuationCreate.noProviderCapability'))).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: i18n.t('valuationCreate.quote.fetch') })).toBeNull()
  })

  it('has no button for a car without a catalogue variant', async () => {
    install()
    renderWithProviders(
      <ValuationCreateDialog
        opened
        onClose={() => {}}
        onCreated={() => {}}
        configuration={{ ...configuration, source: 'manual', catalogueVariantId: null }}
      />,
    )
    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    await waitFor(() => expect(within(dialog).queryByTestId('provider-quote-section')).toBeNull())
  })

  it('says «nicht im Vertrag» when the provider refuses at call time, and «derzeit nicht verfügbar» on an outage', async () => {
    const user = userEvent.setup()
    let answer = 409
    install({
      quote: () =>
        answer === 409
          ? status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'not_entitled' } } })
          : status(422, { error: { code: 'unprocessable_entity', message: 'x', details: { reason: 'provider_unavailable' } } }),
    })
    const dialog = await openDialog()
    await user.click(await within(dialog).findByRole('button', { name: i18n.t('valuationCreate.quote.fetch') }))
    expect(await within(dialog).findByText(i18n.t('valuationCreate.quote.notInContract'))).toBeInTheDocument()

    answer = 422
    await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.quote.fetch') }))
    expect(await within(dialog).findByText(i18n.t('valuationCreate.quote.unavailable'))).toBeInTheDocument()
  })

  it('drops a quote once the mileage changes, and the valuation is then manual', async () => {
    const user = userEvent.setup()
    const backend = install()
    const dialog = await openDialog()
    await user.click(await within(dialog).findByRole('button', { name: i18n.t('valuationCreate.quote.fetch') }))
    await within(dialog).findByTestId('provider-quote')

    const mileage = within(dialog).getByLabelText(i18n.t('valuationCreate.mileage'), { exact: false })
    await user.clear(mileage)
    await user.type(mileage, '52000')

    expect(within(dialog).getByText(i18n.t('valuationCreate.quote.stale'))).toBeInTheDocument()
    expect(within(dialog).queryByTestId('provider-quote')).toBeNull()
    await submitWithOffer(user, dialog)
    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    const body = backend.callsTo(/^\/valuations$/, 'POST')[0].body as Record<string, unknown>
    expect(body.providerQuoteId).toBeNull()
    // the stale quote's figure is not sent as the advisor's own
    expect(body.providerValue).toBeNull()
  })
})

describe('ProviderQuoteCard (KAN-552)', () => {
  it('lays the residual table out as km per year × months, and says why a table is missing', () => {
    installFakeBackend([])
    const { unmount } = renderWithProviders(<ProviderQuoteCard quote={quoteRead()} />)
    const table = screen.getByRole('table', { name: i18n.t('valuationQuote.residualTitle') })
    expect(within(table).getAllByRole('row')).toHaveLength(3) // header + two km bands
    expect(within(table).getByText(i18n.t('valuationQuote.months', { months: 24 }))).toBeInTheDocument()
    unmount()

    renderWithProviders(<ProviderQuoteCard quote={quoteRead({ residualTableState: 'not_in_contract', residualTable: [] })} />)
    expect(screen.getByText(i18n.t('valuationQuote.residualState.not_in_contract'))).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
