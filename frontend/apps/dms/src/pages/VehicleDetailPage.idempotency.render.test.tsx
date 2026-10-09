// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-266 — the vehicle screen's creates (odometer reading, accessory,
// allocation) carry one Idempotency-Key per submission. A successful write
// retires its key: a second identical reading or accessory is a new record,
// never a replay of the first answer that creates nothing.
//
// Not covered here: a retry after a failure keeping its key. The tab forms
// do not catch a failed save (it shows nothing and rejects unhandled), so a
// failing request cannot be rendered yet; the hook's own test pins the
// retry, and the customer screen's render tests pin the same wiring.

const ID = 'veh-1'

function install() {
  const vehicle = {
    id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
    catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
    firstRegistrationDate: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
  const accessories: Record<string, unknown>[] = []
  const readings: Record<string, unknown>[] = []
  return installFakeBackend([
    { match: new RegExp(`^/vehicle-mdm/${ID}$`), handler: () => vehicle },
    { match: new RegExp(`^/vehicle-mdm/${ID}/plates$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/party-roles$`), handler: () => [] },
    { method: 'GET', match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`), handler: () => readings },
    { method: 'GET', match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`), handler: () => accessories },
    {
      method: 'POST',
      match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`),
      handler: (req) => {
        const row = { id: `acc-${accessories.length + 1}`, vehicleId: ID, validTo: null, ...(req.body as object) }
        accessories.push(row)
        return { __status: 201, body: row }
      },
    },
    {
      method: 'POST',
      match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`),
      handler: (req) => {
        const row = { id: `odo-${readings.length + 1}`, vehicleId: ID, implausible: false, ...(req.body as object) }
        readings.push(row)
        return { __status: 201, body: row }
      },
    },
  ])
}

function renderTab(tab: string) {
  renderWithProviders(
    <Routes>
      <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
    </Routes>,
    { route: `/vehicles/${ID}?tab=${tab}` },
  )
}

const keysOf = (backend: ReturnType<typeof install>, segment: string) =>
  backend.callsTo(new RegExp(`/${segment}$`), 'POST').map((call) => call.headers.get('Idempotency-Key'))

afterEach(() => cleanup())

describe('VehicleDetailPage — one Idempotency-Key per create (KAN-266)', () => {
  it('two identical accessories in a row are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderTab('accessories')

    const add = async () => {
      await user.type(await screen.findByLabelText(i18n.t('vehicleDetail.accessories.type')), 'towbar')
      await user.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.accessories.add') }))
    }
    await add()
    await waitFor(() => expect(keysOf(backend, 'accessories')).toHaveLength(1))
    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>(i18n.t('vehicleDetail.accessories.type')).value).toBe(''))
    await add()
    await waitFor(() => expect(keysOf(backend, 'accessories')).toHaveLength(2))

    const [first, second] = keysOf(backend, 'accessories')
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('two identical odometer readings in a row are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderTab('odometer')

    const record = async () => {
      await user.type(await screen.findByLabelText(i18n.t('vehicleDetail.odometer.value')), '42000')
      fireEvent.change(screen.getByLabelText(i18n.t('vehicleDetail.odometer.date')), { target: { value: '2026-01-01' } })
      await user.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.odometer.record') }))
    }
    await record()
    await waitFor(() => expect(keysOf(backend, 'odometer-readings')).toHaveLength(1))
    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>(i18n.t('vehicleDetail.odometer.date')).value).toBe(''))
    await record()
    await waitFor(() => expect(keysOf(backend, 'odometer-readings')).toHaveLength(2))

    const [first, second] = keysOf(backend, 'odometer-readings')
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })
})
