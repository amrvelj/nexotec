// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-351 — since KAN-331 the trade-in refuses a VIN that is still malformed
// after normalising (422, loc [body, vin]). The form said only "save failed";
// it now names the VIN rule at the VIN input, in the user's language.

const OFFER = {
  id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', customerId: null, customerLabel: null,
  vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
  tradeInLabel: null, tradeInVin: null, tradeInValuationId: null, tradeInValue: null, containers: [], version: 4,
} as unknown as SalesOfferRead

const VIN_REFUSED = status(422, {
  error: {
    code: 'unprocessable_entity',
    message: 'Request validation failed.',
    details: { errors: [{ loc: ['body', 'vin'], msg: 'Value error, invalid VIN', type: 'value_error' }] },
  },
})

function install(answer: () => unknown) {
  return installFakeBackend([
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    { method: 'POST', match: /^\/sales\/offers\/o1\/trade-in$/, handler: answer },
  ])
}

async function saveTradeIn(vin: string) {
  const user = userEvent.setup()
  renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
  await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel')), vin)
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.labelLabel')), 'VW Golf')
  await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))
  return user
}

beforeEach(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('OfferWorkspace — a refused trade-in VIN is named at the VIN input (KAN-351)', () => {
  it('a 422 on vin shows the translated VIN rule, not the generic save error', async () => {
    install(() => VIN_REFUSED)
    const user = await saveTradeIn('WVWZZZ1KZAO331338')

    const vinInput = screen.getByLabelText<HTMLInputElement>(i18n.t('offerWorkspace.tradeIn.vinLabel'))
    expect(await screen.findByText(i18n.t('common.invalidVin'))).toBeInTheDocument()
    expect(vinInput).toHaveAttribute('aria-invalid', 'true')
    expect(vinInput.value).toBe('WVWZZZ1KZAO331338')
    expect(screen.queryByText(i18n.t('offerWorkspace.tradeIn.saveError'))).toBeNull()
    expect(screen.queryByText('Request validation failed.')).toBeNull()

    // Correcting the VIN clears the message.
    await user.type(vinInput, '{Backspace}')
    await waitFor(() => expect(screen.queryByText(i18n.t('common.invalidVin'))).toBeNull())
  })

  it('any other failure keeps the generic save error', async () => {
    install(() => status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } }))
    await saveTradeIn('WVWZZZ1KZAW331338')

    expect(await screen.findByText(i18n.t('offerWorkspace.tradeIn.saveError'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('common.invalidVin'))).toBeNull()
  })
})
