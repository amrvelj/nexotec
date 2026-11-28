// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../../../../test/fakeBackend'
import type { ServiceOrderRead, StayRead } from '../../../../api/types'
import { ServiceOrderDetailPage } from '../../pages/ServiceOrderDetailPage'
import { HISTORY, ORDER_ID, order } from '../../test/serviceOrderFixtures'

// KAN-813 (AS1-10): D14 «Fahrzeug ist da» — the mileage required, the last reading beside it, a
// lower one said and taken (check 22); a car that is here is not received again (check 23); the
// reception tab says who, when and how, and lists the orders of the stay; the header shows the
// workshop status the server worked out.

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options)
const ORDER_PATH = new RegExp(`^/service-orders/${ORDER_ID}$`)

const me: FakeRoute = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: {
      id: 'user-1', dealershipId: 'dealership-1', firstName: 'Sandra', lastName: 'Keller', email: 'sk@example.ch', phone: null,
      role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false, employmentStatus: 'active',
      authIdentityId: 'auth-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    },
    activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
    memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ALL_GATED_CAPABILITIES,
  }),
}

const STAY: StayRead = {
  id: 'stay-1',
  arrivedAt: '2026-11-27T07:30:00Z',
  arrivedBy: 'user-1',
  arrivedByLabel: 'Sandra Keller',
  arrivalChannel: 'desk',
  arrivalMileage: 160000,
  previousMileage: 166402,
  mileageBelowPrevious: true,
  keyTag: 'K-17',
  parkingPlace: 'P 4',
  remark: null,
  departedAt: null,
  orders: [
    { id: ORDER_ID, orderNumber: 'WA-2026-0406' },
    { id: 'order-2', orderNumber: 'WA-2026-0407' },
  ],
}

function install(current: ServiceOrderRead, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    me,
    { method: 'GET', match: ORDER_PATH, handler: () => current },
    { method: 'GET', match: /\/history$/, handler: () => HISTORY },
  ])
}

function renderPage(search = '') {
  renderWithProviders(
    <Routes>
      <Route path="/aftersales/orders/:id" element={<ServiceOrderDetailPage />} />
    </Routes>,
    { route: `/aftersales/orders/${ORDER_ID}${search}` },
  )
}

describe('D14 and the reception tab (KAN-813)', () => {
  it('«Fahrzeug ist da» needs the mileage, shows the last reading and sends the arrival under If-Match', async () => {
    const backend = install(order(), [
      { method: 'POST', match: /\/arrival$/, handler: () => order({ stay: STAY, workshopStatus: 'car_here' }) },
    ])
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText(t('serviceOrder.workshopStatus.planned'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('serviceOrder.arrival.title') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByTestId('arrival-last-mileage')).toHaveTextContent("166'402 km")

    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.arrival.submit') }))
    expect(await within(dialog).findByText(t('serviceOrder.arrival.mileageMissing'))).toBeInTheDocument()
    expect(backend.calls.some((c) => c.method === 'POST')).toBe(false)

    await user.type(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.arrival.mileage')) }), '160000')
    expect(within(dialog).getByTestId('arrival-below-last')).toHaveTextContent(t('serviceOrder.arrival.belowLast'))
    await user.type(within(dialog).getByRole('textbox', { name: t('serviceOrder.arrival.keyTag') }), 'K-17')
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.arrival.submit') }))

    await waitFor(() => expect(backend.calls.some((c) => c.method === 'POST')).toBe(true))
    const call = backend.calls.find((c) => c.method === 'POST')!
    expect(call.body).toEqual({ mileage: 160000, keyTag: 'K-17' })
    expect(call.headers.get('If-Match')).toBe('3')
    // The answer is the order as it is now: the car is here, so «Fahrzeug ist da» is disabled.
    await waitFor(() => expect(screen.getByRole('button', { name: t('serviceOrder.arrival.title') })).toBeDisabled())
  })

  it('a car that is here is not received again: the action is disabled with its reason', async () => {
    install(order({ stay: STAY, workshopStatus: 'car_here' }))
    renderPage()
    expect(await screen.findByRole('button', { name: t('serviceOrder.arrival.title') })).toBeDisabled()
  })

  it('a refusal is said in one sentence in the UI language', async () => {
    install(order(), [
      {
        method: 'POST',
        match: /\/arrival$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'service_stay_car_already_here' } } }),
      },
    ])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: t('serviceOrder.arrival.title') }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(t('serviceOrder.arrival.mileage')) }), '1')
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.arrival.submit') }))
    expect(await within(dialog).findByText(t('apiRefusal.reasons.service_stay_car_already_here'))).toBeInTheDocument()
  })

  it('the reception tab says who received the car, when and how, flags a lower mileage and lists the stay\'s orders', async () => {
    install(order({ stay: STAY, workshopStatus: 'car_here' }))
    renderPage('?tab=reception')

    const reception = await screen.findByTestId('reception')
    expect(within(reception).getByTestId('reception-received')).toHaveTextContent(
      `Sandra Keller · ${t('serviceOrder.reception.channel.desk')}`,
    )
    expect(within(reception).getByTestId('reception-mileage')).toHaveTextContent("160'000 km")
    expect(within(reception).getByTestId('reception-mileage')).toHaveTextContent(
      t('serviceOrder.reception.belowPrevious', { km: "166'402" }),
    )
    expect(within(reception).getByText('K-17')).toBeInTheDocument()
    expect(within(reception).getByRole('link', { name: 'WA-2026-0407' })).toHaveAttribute('href', '/aftersales/orders/order-2')
  })

  it('without a stay the reception tab says the car is not here yet', async () => {
    install(order())
    renderPage('?tab=reception')
    expect(await screen.findByText(t('serviceOrder.reception.notHere'))).toBeInTheDocument()
  })
})
