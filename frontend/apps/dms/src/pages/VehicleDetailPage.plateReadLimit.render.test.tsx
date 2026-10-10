// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-231 — past the per-user plate-read limit the API refuses /plates
// (403, `plate_read_limit_reached`). The car still opens; the Plates tab
// says why there is nothing, in the user's language, and the refusal is
// not retried (each attempt is audited server-side).

const ID = 'veh-1'

function install() {
  const vehicle = {
    id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
    catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
    firstRegistrationDate: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
  return installFakeBackend([
    { match: new RegExp(`^/vehicle-mdm/${ID}$`), handler: () => vehicle },
    {
      match: new RegExp(`^/vehicle-mdm/${ID}/plates$`),
      handler: () => ({
        __status: 403,
        body: {
          error: {
            code: 'forbidden',
            message: "Too many vehicles' plates read in a short time; try again later.",
            details: { reason: 'plate_read_limit_reached' },
          },
        },
      }),
    },
    { match: new RegExp(`^/vehicle-mdm/${ID}/party-roles$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`), handler: () => [] },
  ])
}

describe('VehicleDetailPage — plate-read limit (KAN-231)', () => {
  afterEach(async () => {
    cleanup()
    await i18n.changeLanguage('de')
  })

  it.each([
    ['de', 'Sie haben in kurzer Zeit die Kennzeichen vieler Fahrzeuge angesehen. Bitte versuchen Sie es später erneut.'],
    ['en', 'You have viewed many licence plates in a short time. Try again later.'],
  ])('opens the car and explains the refusal on the Plates tab (%s)', async (language, message) => {
    await i18n.changeLanguage(language)
    const backend = install()

    renderWithProviders(
      <Routes>
        <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
      </Routes>,
      { route: `/vehicles/${ID}?tab=plates` },
    )

    expect(await screen.findByText(message)).toBeTruthy()
    expect(screen.getByText('F-000001')).toBeTruthy()
    // The page's own retry policy, not the test client's: wait out the
    // first retry delay (1s) and check the refusal was asked once.
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'GET')).toHaveLength(1)
  })
})
