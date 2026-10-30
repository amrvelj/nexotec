// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-468 (Anto, 2026-10-26; FR-V-16 amended, ADR-061): Vehicle 360 has no
// «Edit» — the identity fields are inline. The primary «Allocate to
// customer» opens the Identity tab's customer picker from any tab; the
// alternative «Request valuation» starts a new valuation of this car.

const ID = 'veh-1'

const vehicle = {
  id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
  catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
  firstRegistrationDate: '1999-04-01', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

const session = (entitlements: string[]): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', firstName: 'Test', lastName: 'User', email: 'test@example.ch', accessRoles: [] },
    activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
    memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
    entitlements,
  }),
})

function install(entitlements?: string[]) {
  return installFakeBackend([
    ...(entitlements ? [session(entitlements)] : []),
    { match: new RegExp(`^/vehicle-mdm/${ID}$`), handler: () => vehicle },
    { match: new RegExp(`^/vehicle-mdm/${ID}/plates$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/party-roles$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`), handler: () => [] },
  ])
}

function Where() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

function render(route = `/vehicles/${ID}`) {
  renderWithProviders(
    <Routes>
      <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
      <Route path="/valuations/new" element={<Where />} />
    </Routes>,
    { route },
  )
}

// The Identity tab's party card has its own «Allocate to customer» button;
// the header's is the filled one.
async function headerPrimary() {
  const buttons = await screen.findAllByRole('button', { name: i18n.t('vehicleDetail.header.allocate') })
  const primary = buttons.find((b) => b.getAttribute('data-variant') === 'filled')
  expect(primary).toBeDefined()
  return primary!
}

afterEach(() => cleanup())

describe('Vehicle 360 header (KAN-468)', () => {
  it('has no «Edit»: «Allocate to customer» is the primary, «Request valuation» the alternative', async () => {
    const user = userEvent.setup()
    install()
    render()

    await headerPrimary()
    expect(screen.getByRole('button', { name: i18n.t('vehicleDetail.header.requestValuation') })).toHaveAttribute(
      'data-variant',
      'default',
    )
    expect(screen.queryByRole('button', { name: /^(Edit|Bearbeiten)$/ })).toBeNull()

    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    for (const key of ['recordOdometer', 'recordAccessory', 'resync', 'merge', 'copyVin']) {
      expect(
        await screen.findByRole('menuitem', { name: new RegExp(i18n.t(`vehicleDetail.header.${key}`)) }),
      ).toBeInTheDocument()
    }
  })

  it('the primary opens the customer picker, from any tab — never a no-op tab switch', async () => {
    const user = userEvent.setup()
    install()
    render(`/vehicles/${ID}?tab=odometer`)

    await user.click(await headerPrimary())

    expect(await screen.findByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer'))).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('vehicleDetail.tabs.identity')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )

    // On the Identity tab itself it opens the picker too.
    await user.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))
    expect(screen.queryByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer'))).toBeNull()
    await user.click(await headerPrimary())
    expect(await screen.findByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer'))).toBeInTheDocument()
  })

  it('«Request valuation» opens a new valuation of this car', async () => {
    const user = userEvent.setup()
    install()
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.header.requestValuation') }))

    expect(await screen.findByTestId('location')).toHaveTextContent(`/valuations/new?vehicle=${ID}`)
  })

  it('a Betrieb without sales: «Allocate to customer» is the primary and there is no valuation', async () => {
    const user = userEvent.setup()
    install(['aftersales', 'parts'])
    render()

    await headerPrimary()
    expect(screen.queryByRole('button', { name: i18n.t('vehicleDetail.header.requestValuation') })).toBeNull()
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    await screen.findByRole('menuitem', { name: new RegExp(i18n.t('vehicleDetail.header.recordOdometer')) })
    expect(
      screen.queryByRole('menuitem', { name: new RegExp(i18n.t('vehicleDetail.header.requestValuation')) }),
    ).toBeNull()
  })
})
