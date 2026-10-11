// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import en from '../i18n/locales/en.json'
import { installFakeBackend, status } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-266 — the vehicle screen's creates (odometer reading, accessory,
// allocation) carry one Idempotency-Key per submission. A successful write
// retires its key: a second identical reading, accessory or allocation is a
// new submission, never a replay of the first answer that writes nothing.
//
// KAN-269 — a failed save on these tabs shows why, keeps what was typed and
// leaves its busy state; the retry carries the failed request's key.

const ID = 'veh-1'

type FailOn = 'odometer-readings' | 'accessories' | 'allocate' | 'accessory-remove'

// A gateway's timeout page: no API error envelope, so the screen must fall
// back to a translated message (KAN-271).
const gatewayTimeout = () =>
  new Response('<html><body>504 Gateway Time-out</body></html>', { status: 504, headers: { 'content-type': 'text/html' } })

function install({ failFirst }: { failFirst?: FailOn } = {}) {
  const failed = new Set<FailOn>()
  // The first request of the named kind fails; every later one succeeds.
  const failOnce = (kind: FailOn, failure: () => unknown) => {
    if (failFirst !== kind || failed.has(kind)) return null
    failed.add(kind)
    return failure()
  }
  const vehicle = {
    id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
    catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
    firstRegistrationDate: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
  const accessories: Record<string, unknown>[] = failFirst === 'accessory-remove'
    ? [{ id: 'acc-0', vehicleId: ID, accessoryType: 'towbar', description: null, validFrom: '2026-01-01', validTo: null }]
    : []
  const readings: Record<string, unknown>[] = []
  const anna = customer({ id: 'cust-1', firstName: 'Anna', lastName: 'Muster', customerNumber: 'K-000001' })
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
        const failure = failOnce('accessories', () => status(503, { error: { code: 'unavailable', message: 'Accessory store unavailable', details: null } }))
        if (failure) return failure
        const row = { id: `acc-${accessories.length + 1}`, vehicleId: ID, validTo: null, ...(req.body as object) }
        accessories.push(row)
        return { __status: 201, body: row }
      },
    },
    { method: 'GET', match: /^\/customers$/, handler: () => ({ items: [anna], nextCursor: null }) },
    {
      method: 'POST',
      match: new RegExp(`^/vehicle-mdm/${ID}/allocate$`),
      handler: (req) =>
        failOnce('allocate', () => status(409, { error: { code: 'conflict', message: 'Allocation refused', details: null } })) ?? {
          __status: 201,
          body: { id: 'party-1', vehicleId: ID, effectiveFrom: '2026-01-01T00:00:00Z', effectiveTo: null, ...(req.body as object) },
        },
    },
    {
      method: 'DELETE',
      match: new RegExp(`^/vehicle-mdm/${ID}/accessories/[^/]+$`),
      handler: () => failOnce('accessory-remove', gatewayTimeout) ?? status(204),
    },
    {
      method: 'POST',
      match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`),
      handler: (req) => {
        const failure = failOnce('odometer-readings', gatewayTimeout)
        if (failure) return failure
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

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

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

  it('allocating the same customer twice in a row is two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderTab('identity')

    const allocate = async () => {
      await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.parties.allocate') }))
      await user.type(screen.getByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer')), 'Muster')
      await user.click(await screen.findByText('Anna Muster'))
    }
    await allocate()
    await waitFor(() => expect(keysOf(backend, 'allocate')).toHaveLength(1))
    await allocate()
    await waitFor(() => expect(keysOf(backend, 'allocate')).toHaveLength(2))

    expect(backend.callsTo(/\/allocate$/, 'POST')[0].body).toEqual({ customerId: 'cust-1', role: 'owner' })
    const [first, second] = keysOf(backend, 'allocate')
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })
})

describe('VehicleDetailPage — a failed save says so and keeps its key (KAN-269, KAN-271)', () => {
  it('a failed odometer reading shows a translated message in fr, keeps the input, and the retry reuses the key', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const backend = install({ failFirst: 'odometer-readings' })
    renderTab('odometer')

    await user.type(await screen.findByLabelText(i18n.t('vehicleDetail.odometer.value')), '42000')
    fireEvent.change(screen.getByLabelText(i18n.t('vehicleDetail.odometer.date')), { target: { value: '2026-01-01' } })
    const record = screen.getByRole('button', { name: i18n.t('vehicleDetail.odometer.record') })
    await user.click(record)

    // A 504 with an HTML body: no API message, so the translated fallback —
    // never the English one (KAN-271).
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(i18n.t('common.requestFailed'))
    expect(alert.textContent).not.toBe(en.common.requestFailed)
    expect(screen.getByLabelText<HTMLInputElement>(i18n.t('vehicleDetail.odometer.date')).value).toBe('2026-01-01')
    await waitFor(() => expect(record).not.toHaveAttribute('data-loading'))

    await user.click(record)
    await waitFor(() => expect(keysOf(backend, 'odometer-readings')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    const [failedKey, retryKey] = keysOf(backend, 'odometer-readings')
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a failed accessory shows the server message, keeps the input, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: 'accessories' })
    renderTab('accessories')

    await user.type(await screen.findByLabelText(i18n.t('vehicleDetail.accessories.type')), 'towbar')
    await user.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.accessories.add') }))

    expect((await screen.findByRole('alert')).textContent).toBe('Accessory store unavailable')
    expect(screen.getByLabelText<HTMLInputElement>(i18n.t('vehicleDetail.accessories.type')).value).toBe('towbar')

    await user.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.accessories.add') }))
    await waitFor(() => expect(keysOf(backend, 'accessories')).toHaveLength(2))
    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>(i18n.t('vehicleDetail.accessories.type')).value).toBe(''))
    expect(screen.queryByRole('alert')).toBeNull()

    const [failedKey, retryKey] = keysOf(backend, 'accessories')
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a failed accessory removal shows a translated message', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    install({ failFirst: 'accessory-remove' })
    renderTab('accessories')

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.accessories.remove') }))
    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('common.requestFailed'))
  })

  it('a failed allocation shows the server message, leaves the picker usable, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: 'allocate' })
    renderTab('identity')

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.parties.allocate') }))
    await user.type(screen.getByPlaceholderText(i18n.t('vehicleDetail.parties.searchCustomer')), 'Muster')
    await user.click(await screen.findByText('Anna Muster'))

    expect((await screen.findByRole('alert')).textContent).toBe('Allocation refused')
    // Not busy any more: the picker lists the customer again and can be retried.
    await user.click(await screen.findByText('Anna Muster'))
    await waitFor(() => expect(keysOf(backend, 'allocate')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(await screen.findByRole('button', { name: i18n.t('vehicleDetail.parties.allocate') })).toBeInTheDocument()

    const [failedKey, retryKey] = keysOf(backend, 'allocate')
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})
