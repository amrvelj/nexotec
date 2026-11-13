// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { ApiError } from '../api/client'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { apiRefusalText } from '../utils/apiRefusal'
import { readStockOpenAction } from '../components/stockRowMenu'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-667 (Anto, 2026-11-13, option A): on a car the session's own Betrieb
// holds in stock, the VIN on Vehicle 360 is read-only and links to that stock
// car's «VIN korrigieren» (KAN-557). The server refuses the plain PATCH with
// a translated reason. Another Betrieb's stock never shows: the API leaves
// `stockItem` null, and the VIN stays editable.

const ID = 'veh-1'
const STOCK_ID = 'stock-1'

const base = {
  id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
  catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
  firstRegistrationDate: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

const session = (entitlements: string[], writableCapabilities: string[]): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', firstName: 'Test', lastName: 'User', email: 'test@example.ch', accessRoles: [] },
    activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
    memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
    entitlements,
    writableCapabilities,
  }),
})

function install(
  stockItem: { id: string; stockNumber: string | null } | null,
  entitlements = ['sales'],
  writableCapabilities = ['vehicle_mdm', 'stock_vin_correction'],
) {
  return installFakeBackend([
    session(entitlements, writableCapabilities),
    { match: new RegExp(`^/vehicle-mdm/${ID}$`), handler: () => ({ ...base, stockItem }) },
    { match: new RegExp(`^/vehicle-mdm/${ID}/plates$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/party-roles`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`), handler: () => [] },
  ])
}

function StockDetailStandIn() {
  const location = useLocation()
  return (
    <div data-testid="stock-detail">
      {location.pathname} {readStockOpenAction(location.state) ?? 'none'}
    </div>
  )
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
      <Route path="/stock/:id" element={<StockDetailStandIn />} />
    </Routes>,
    { route: `/vehicles/${ID}` },
  )
}

afterEach(() => cleanup())

describe('Vehicle 360 VIN of a car in the Betrieb’s stock (KAN-667)', () => {
  it('is read-only and opens the stock car at «VIN korrigieren»', async () => {
    await i18n.changeLanguage('de')
    const user = userEvent.setup()
    install({ id: STOCK_ID, stockNumber: 'L-000042' })
    render()

    expect(await screen.findByTestId('vin-read-only')).toHaveTextContent(base.vin)
    // No inline edit on the VIN: a click on it opens no input.
    await user.click(screen.getByTestId('vin-read-only'))
    expect(screen.queryByRole('textbox')).toBeNull()

    const link = screen.getByRole('button', { name: 'VIN am Lagerfahrzeug L-000042 korrigieren' })
    await user.click(link)

    expect(await screen.findByTestId('stock-detail')).toHaveTextContent(`/stock/${STOCK_ID} correctVin`)
  })

  it('names the stock car without a number when no fact carried one', async () => {
    await i18n.changeLanguage('de')
    install({ id: STOCK_ID, stockNumber: null })
    render()

    expect(await screen.findByRole('button', { name: 'VIN am Lagerfahrzeug korrigieren' })).toBeInTheDocument()
  })

  it('offers no link without the Stock module, the VIN still read-only', async () => {
    await i18n.changeLanguage('de')
    install({ id: STOCK_ID, stockNumber: 'L-000042' }, [])
    render()

    expect(await screen.findByTestId('vin-read-only')).toHaveTextContent(base.vin)
    expect(screen.queryByRole('button', { name: /Lagerfahrzeug/ })).toBeNull()
  })

  it('offers no link to a user who may not correct a VIN (dealer manager only), the VIN still read-only', async () => {
    await i18n.changeLanguage('de')
    install({ id: STOCK_ID, stockNumber: 'L-000042' }, ['sales'], ['vehicle_mdm'])
    render()

    expect(await screen.findByTestId('vin-read-only')).toHaveTextContent(base.vin)
    expect(screen.queryByRole('button', { name: /Lagerfahrzeug/ })).toBeNull()
  })

  it('stays editable when the Betrieb holds no stock car of it (another Betrieb’s never shows)', async () => {
    await i18n.changeLanguage('de')
    const user = userEvent.setup()
    install(null)
    render()

    // The header names the VIN too; the Identity tab's field comes last.
    const shown = await screen.findAllByText(base.vin, { selector: 'span' })
    await user.click(shown[shown.length - 1])
    expect(screen.getByRole('textbox')).toHaveValue(base.vin)
    expect(screen.queryByTestId('vin-read-only')).toBeNull()
    expect(screen.queryByRole('button', { name: /Lagerfahrzeug/ })).toBeNull()
  })

  it.each(['de', 'fr', 'it', 'en'])('says the refusal in %s, naming the stock car', async (language) => {
    await i18n.loadLanguages(language)
    const t = i18n.getFixedT(language)
    const refusal = new ApiError(409, 'conflict', 'This car is in your stock.', {
      reason: 'vin_change_needs_vin_correction',
      field: 'vin',
      stockItemId: STOCK_ID,
      stockNumber: 'L-000042',
    })

    const text = apiRefusalText(t, refusal, 'common.inlineEdit.saveFailed')

    expect(text).toContain('L-000042')
    expect(text).not.toBe(t('common.inlineEdit.saveFailed'))
    expect(text).not.toContain('MISSING')
    expect(t('vehicleDetail.identity.vinHeldInStock')).not.toContain('MISSING')
  })
})
