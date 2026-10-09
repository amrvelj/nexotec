// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import { VehicleCreateDialog } from './VehicleCreateDialog'

// KAN-266 — POST /vehicle-mdm carries one Idempotency-Key per submission:
// a retry after a failure (a response lost on the way back) gets the first
// answer instead of running again; another VIN, or another opening of the
// dialog, is a new submission.

const VIN = 'WVWZZZ1JZXW000001'
const OTHER_VIN = 'ZAR94000007123456'

function vehicle(vin: string) {
  return {
    id: `veh-${vin}`, vin, vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
    firstRegistrationDate: null, catalogueVariantId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
    mergedIntoVehicleId: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
}

// The first POST fails; every later one creates.
function renderDialog() {
  let attempts = 0
  const backend = installFakeBackend([
    {
      method: 'POST',
      match: /^\/vehicle-mdm$/,
      handler: (req) => {
        attempts += 1
        if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
        return { created: true, vehicle: vehicle((req.body as { vin: string }).vin) }
      },
    },
  ])
  const onCreated = vi.fn()
  function Harness() {
    const [opened, setOpened] = useState(true)
    return (
      <>
        <button type="button" onClick={() => setOpened(true)}>reopen</button>
        <VehicleCreateDialog opened={opened} onClose={() => setOpened(false)} onCreated={onCreated} />
      </>
    )
  }
  renderWithProviders(<Harness />)
  const keys = () => backend.callsTo(/^\/vehicle-mdm$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
  return { keys, onCreated }
}

const vinField = () => screen.getByLabelText(i18n.t('vehicleCreate.vinLabel'))
const submit = () => userEvent.click(screen.getByRole('button', { name: i18n.t('vehicleCreate.submit') }))

afterEach(() => cleanup())

describe('VehicleCreateDialog — one Idempotency-Key per submission (KAN-266)', () => {
  it('a failed create shows why, and its retry carries the same key', async () => {
    const { keys, onCreated } = renderDialog()
    await userEvent.type(vinField(), VIN)

    await submit()
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again.')
    await submit()
    await waitFor(() => expect(onCreated).toHaveBeenCalled())

    expect(keys()).toHaveLength(2)
    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBe(keys()[0])
  })

  it('another VIN after a failure is another request and gets a new key', async () => {
    const { keys } = renderDialog()
    await userEvent.type(vinField(), VIN)
    await submit()
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await userEvent.clear(vinField())
    await userEvent.type(vinField(), OTHER_VIN)
    await submit()
    await waitFor(() => expect(keys()).toHaveLength(2))

    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })

  it('the same VIN sent after closing and reopening the dialog gets a new key', async () => {
    const { keys } = renderDialog()
    await userEvent.type(vinField(), VIN)
    await submit()
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'reopen' }))
    await userEvent.type(vinField(), VIN)
    await submit()
    await waitFor(() => expect(keys()).toHaveLength(2))

    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})
