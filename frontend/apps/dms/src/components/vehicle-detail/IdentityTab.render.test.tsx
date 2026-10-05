// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
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

function renderTab(parties: VehiclePartyAllocationRead[], formerParties: VehiclePartyAllocationRead[] = []) {
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
    />,
  )
}

describe('IdentityTab party rows show the holder, never the customer id (KAN-140)', () => {
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
})
