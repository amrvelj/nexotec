// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { StockCreatePage } from './StockCreatePage'

// KAN-111: a VIN already on a stock item that has not left stock is
// refused with 409 `vin_already_in_stock`. The page says so in the user's
// language and links to the car that holds the VIN (Anto, 2026-10-07);
// the backend's English message is never shown.

function refuseCreate(error: { status: number; body: unknown }) {
  return installFakeBackend([
    { method: 'POST', match: /^\/inventory\/stock-items$/, handler: () => status(error.status, error.body) },
  ])
}

const VIN_IN_STOCK = {
  status: 409,
  body: {
    error: {
      code: 'conflict',
      message: 'This VIN is already in stock as S-000123.',
      details: { reason: 'vin_already_in_stock', stockItemId: 'held-1', stockNumber: 'S-000123' },
    },
  },
}

async function submitWithVin() {
  const user = userEvent.setup()
  renderWithProviders(
    <Routes>
      <Route path="/stock/new" element={<StockCreatePage />} />
      <Route path="/stock/:id" element={<p>stock detail page</p>} />
    </Routes>,
    { route: '/stock/new' },
  )
  await user.type(screen.getByLabelText(i18n.t('stockCreate.fields.vehicleLabel'), { exact: false }), 'VW Golf')
  await user.type(screen.getByLabelText(i18n.t('stockCreate.fields.vin')), 'WVWZZZ1KZAW000001')
  await user.click(screen.getByRole('button', { name: i18n.t('stockCreate.submit') }))
  return user
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('StockCreatePage — a VIN already in stock (KAN-111)', () => {
  it('names the stock item that holds the VIN, localised, and links to it', async () => {
    refuseCreate(VIN_IN_STOCK)
    const user = await submitWithVin()

    expect(
      await screen.findByText(i18n.t('stockCreate.errors.vinAlreadyInStock', { stockNumber: 'S-000123' })),
    ).toBeInTheDocument()
    expect(screen.queryByText('This VIN is already in stock as S-000123.')).not.toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: i18n.t('stockCreate.errors.openExisting') }))
    expect(await screen.findByText('stock detail page')).toBeInTheDocument()
  })

  it('is localised in French too', async () => {
    await i18n.changeLanguage('fr')
    refuseCreate(VIN_IN_STOCK)
    await submitWithVin()

    const fr = i18n.t('stockCreate.errors.vinAlreadyInStock', { stockNumber: 'S-000123' })
    expect(fr).not.toBe(i18n.getFixedT('de')('stockCreate.errors.vinAlreadyInStock', { stockNumber: 'S-000123' }))
    expect(await screen.findByText(fr)).toBeInTheDocument()
  })

  it('any other failure keeps the generic message and offers no link', async () => {
    refuseCreate({ status: 500, body: { error: { code: 'internal', message: 'boom', details: null } } })
    await submitWithVin()

    expect(await screen.findByText(i18n.t('stockCreate.errors.somethingWentWrong'))).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})

describe('StockCreatePage — one Idempotency-Key per submission (KAN-266)', () => {
  // The first create fails; every later one succeeds.
  function installFailingFirstCreate() {
    let attempts = 0
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /^\/inventory\/stock-items$/,
        handler: () => {
          attempts += 1
          if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
          return status(201, { id: 'si-1' })
        },
      },
    ])
    return () => backend.callsTo(/^\/inventory\/stock-items$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
  }

  it('a create retried after a failure carries the same key', async () => {
    const keys = installFailingFirstCreate()
    const user = await submitWithVin()
    expect(await screen.findByText(i18n.t('stockCreate.errors.somethingWentWrong'))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('stockCreate.submit') }))

    expect(await screen.findByText('stock detail page')).toBeInTheDocument()
    expect(keys()).toHaveLength(2)
    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBe(keys()[0])
  })

  it('a corrected form after a failure is another request and gets a new key', async () => {
    const keys = installFailingFirstCreate()
    const user = await submitWithVin()
    expect(await screen.findByText(i18n.t('stockCreate.errors.somethingWentWrong'))).toBeInTheDocument()

    await user.type(screen.getByLabelText(i18n.t('stockCreate.fields.vehicleLabel'), { exact: false }), ' Variant')
    await user.click(screen.getByRole('button', { name: i18n.t('stockCreate.submit') }))

    expect(await screen.findByText('stock detail page')).toBeInTheDocument()
    expect(keys()).toHaveLength(2)
    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})
