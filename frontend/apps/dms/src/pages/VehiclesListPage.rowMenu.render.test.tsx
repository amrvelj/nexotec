// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { VehicleMdmRead, VehicleSearchResult } from '../api/types'
import { VehicleDetailPage } from './VehicleDetailPage'
import { VehiclesListPage } from './VehiclesListPage'

// KAN-503 (ADR-061, FR-V-16): the Vehicles list's rows carry the row menu
// Vehicle 360's header renders from — «Öffnen», then buildVehicleRowMenu's
// overflow, same items in the same order. The actions that need the detail
// screen's state open the car right there.

// jsdom has no layout, so TanStack Virtual windows down to zero rows and no
// row menu would render: render every row, as in listRowActions.failure.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const ID = 'veh-1'

const vehicle = {
  id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
  catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
  firstRegistrationDate: '1999-04-01', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
} as VehicleMdmRead

const searchPage: VehicleSearchResult = {
  resolved: null,
  pickerCandidates: [],
  filtered: { items: [vehicle], nextCursor: null, total: 1, totalIsEstimate: false },
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
    { match: /^\/vehicle-mdm\/search$/, handler: () => searchPage },
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

// The router state the detail screen sees, so a test can tell that the
// list's action was used up.
function DetailWithState() {
  const location = useLocation()
  return (
    <>
      <div data-testid="router-state">{JSON.stringify(location.state)}</div>
      <VehicleDetailPage />
    </>
  )
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/vehicles" element={<VehiclesListPage />} />
      <Route path="/vehicles/:id" element={<DetailWithState />} />
      <Route path="/valuations/new" element={<Where />} />
    </Routes>,
    { route: '/vehicles' },
  )
}

const label = (key: string) => i18n.t(`vehicleDetail.header.${key}`)

async function openRowMenu(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('F-000001')
  const [menuButton] = await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
  await user.click(menuButton)
  return screen.findByRole('menu')
}

const item = (key: string) => screen.findByRole('menuitem', { name: new RegExp(label(key)) })

afterEach(() => cleanup())

describe('Vehicles list — the row menu is Vehicle 360\'s (KAN-503)', () => {
  it('lists «Öffnen», then the detail\'s overflow in the same order, the unbuilt ones disabled', async () => {
    const user = userEvent.setup()
    install()
    render()

    const menu = await openRowMenu(user)
    const names = within(menu)
      .getAllByRole('menuitem')
      .map((entry) => entry.textContent ?? '')
    const expected = [
      i18n.t('vehiclesList.rowActions.open'),
      label('allocate'),
      label('recordOdometer'),
      label('recordAccessory'),
      // KAN-467
      label('recordPlate'),
      label('resync'),
      label('requestValuation'),
      label('copyVin'),
      label('merge'),
    ]
    expect(names).toHaveLength(expected.length)
    expected.forEach((name, index) => expect(names[index]).toContain(name))
    expect(await item('resync')).toHaveAttribute('data-disabled')
    expect(await item('merge')).toHaveAttribute('data-disabled')
  })

  it('«Kunde zuordnen» opens the car with the Identity tab\'s customer picker open', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('allocate'))

    expect(await screen.findByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer'))).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('vehicleDetail.tabs.identity')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('«Kilometerstand erfassen» opens the car at its focused form', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('recordOdometer'))

    await waitFor(() => expect(screen.getByLabelText(i18n.t('vehicleDetail.odometer.value'))).toHaveFocus())
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('vehicleDetail.tabs.odometer')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('«Zubehör erfassen» opens the car at its focused form', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('recordAccessory'))

    await waitFor(() => expect(screen.getByLabelText(i18n.t('vehicleDetail.accessories.type'))).toHaveFocus())
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('vehicleDetail.tabs.accessories')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('«Kontrollschild erfassen» opens the car at the Plates tab with the recording dialog open (KAN-467)', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('recordPlate'))

    expect(await screen.findByRole('dialog', { name: i18n.t('vehicleDetail.plates.recordTitle') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('vehicleDetail.tabs.plates')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('the action is used up once it has run: a reload, Back or Forward never repeats it', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('allocate'))

    expect(await screen.findByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer'))).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('router-state')).toHaveTextContent('null'))
  })

  it('«VIN kopieren» copies that row\'s VIN without leaving the list', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn(() => Promise.resolve())
    install()
    render()
    // user-event installs its own clipboard on setup; replace it after.
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    await openRowMenu(user)
    await user.click(await item('copyVin'))

    expect(writeText).toHaveBeenCalledWith(vehicle.vin)
    expect(screen.getByText('F-000001')).toBeInTheDocument()
  })

  it('«Bewertung anfordern» starts a new valuation of that car', async () => {
    const user = userEvent.setup()
    install()
    render()

    await openRowMenu(user)
    await user.click(await item('requestValuation'))

    expect(await screen.findByTestId('location')).toHaveTextContent(`/valuations/new?vehicle=${ID}`)
  })

  it('a Betrieb without sales has no valuation entry, as on the detail', async () => {
    const user = userEvent.setup()
    install(['aftersales', 'parts'])
    render()

    const menu = await openRowMenu(user)
    expect(within(menu).getByRole('menuitem', { name: new RegExp(label('allocate')) })).toBeInTheDocument()
    expect(within(menu).queryByRole('menuitem', { name: new RegExp(label('requestValuation')) })).toBeNull()
  })
})
