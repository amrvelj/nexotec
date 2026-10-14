// @vitest-environment jsdom
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { IdentityTab } from './IdentityTab'
import type { VehicleMdmRead, VehiclePartyAllocationRead } from '../../api/types'

// KAN-140 — the "Beteiligte" card used to print each holder's raw customer
// UUID as the link text. A seller must see who owns, keeps or drives the
// car, current and former, without opening the overlay.

const UUID_LIKE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

const vehicle: VehicleMdmRead = {
  id: '01a0af73-6570-7571-9170-ec0f5b17f9ac',
  vehicleNumber: 'F-000002',
  vin: 'WBA4Y9F55LCE89GLA',
  stammnummer: null,
  typeApprovalNumber: null,
  catalogueVariantId: null,
  mergedIntoVehicleId: null,
  catalogueMatchStatus: 'unverified',
  vehicleStatus: 'active',
  firstRegistrationDate: null,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

function party(over: Partial<VehiclePartyAllocationRead>): VehiclePartyAllocationRead {
  return {
    id: 'p1',
    vehicleId: vehicle.id,
    customerId: '01a105b3-f856-7cdc-938f-a2ddac5ca8f8',
    role: 'keeper',
    effectiveFrom: '2026-01-01T00:00:00Z',
    effectiveTo: null,
    displayName: 'Leasing AG',
    ...over,
  }
}

function renderTab(
  parties: VehiclePartyAllocationRead[],
  formerParties: VehiclePartyAllocationRead[] = [],
  onCustomerOverlayClose: () => void = () => {},
) {
  return renderWithProviders(
    <IdentityTab
      vehicle={vehicle}
      parties={parties}
      formerParties={formerParties}
      onSaveField={async () => {}}
      onReload={() => {}}
      onAllocate={async () => {}}
      customerCandidates={[]}
      customerSearch=""
      onCustomerSearchChange={() => {}}
      onCustomerOverlayClose={onCustomerOverlayClose}
    />,
  )
}

describe('IdentityTab party rows show the holder, never the customer id (KAN-140)', () => {
  // KAN-172 — renderWithProviders' AuthProvider and preference scope fetch on
  // mount; every test gets the fake backend, not the network.
  beforeEach(() => {
    installFakeBackend([])
  })

  it('names every current and former holder', () => {
    void i18n.changeLanguage('de')
    const { container } = renderTab(
      [party({})],
      [party({ id: 'p0', customerId: '01a105b3-0000-7cdc-938f-a2ddac5ca8f8', displayName: 'Frieda Former', effectiveTo: '2026-02-01T00:00:00Z' })],
    )

    expect(screen.getByRole('button', { name: 'Leasing AG' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.parties.showFormer') }))
    expect(screen.getByRole('button', { name: 'Frieda Former' })).toBeTruthy()

    expect(container.textContent).not.toMatch(UUID_LIKE)
  })

  it('falls back to a translated label, not the id, when a row carries no name', () => {
    void i18n.changeLanguage('de')
    const { container } = renderTab([party({ displayName: null })])

    expect(screen.getByRole('button', { name: i18n.t('vehicleDetail.parties.unnamedCustomer') })).toBeTruthy()
    expect(container.textContent).not.toMatch(UUID_LIKE)
  })

  it('asks the page to refetch the party roles when the customer overlay closes (U-11)', () => {
    // The overlaid Customer 360 can rename the holder inline; the card must
    // not keep showing the old name once the overlay is closed.
    const onClose = vi.fn()
    renderTab([party({})], [], onClose)

    fireEvent.click(screen.getByRole('button', { name: 'Leasing AG' }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

// KAN-333 — the first registration date is date-only: it showed raw ISO
// (`2021-04-01`); now dd.MM.yyyy, the recorded day even west of Greenwich,
// while the editor still opens on the ISO value the API takes.
describe('IdentityTab first registration date (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders dd.MM.yyyy as the recorded day', () => {
    installFakeBackend([])
    renderWithProviders(
      <IdentityTab
        vehicle={{ ...vehicle, firstRegistrationDate: '2021-04-01' }}
        parties={[]}
        formerParties={[]}
        onSaveField={async () => {}}
        onReload={() => {}}
        onAllocate={async () => {}}
        customerCandidates={[]}
        customerSearch=""
        onCustomerSearchChange={() => {}}
        onCustomerOverlayClose={() => {}}
      />,
    )
    expect(screen.getByText('01.04.2021')).toBeInTheDocument()
    expect(screen.queryByText('2021-04-01')).not.toBeInTheDocument()
    expect(screen.queryByText('31.03.2021')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('01.04.2021'))
    expect(screen.getByDisplayValue('2021-04-01')).toBeInTheDocument()
  })
})
