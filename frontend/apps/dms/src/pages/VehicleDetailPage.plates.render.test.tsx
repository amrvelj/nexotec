// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { VehicleDetailPage } from './VehicleDetailPage'

// KAN-467 — a licence plate could not be recorded anywhere in the product.
// «Kontrollschild erfassen» on the Plates tab (and in the overflow) records
// one; a plate current on another car of the group is refused naming that
// car, and the user's «move it here» goes back on the same POST (Anto,
// 2026-10-31); «beenden» ends a current one under If-Match.

const ID = 'veh-1'
const vehicle = {
  id: ID, vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
  catalogueVariantId: null, mergedIntoVehicleId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
  firstRegistrationDate: '1999-04-01', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}
const current = {
  id: 'p-1', plate: 'BE 4711', canton: 'BE', validFrom: '2024-03-01', validTo: null, isInterchangeable: false,
  plateGroupId: null, version: 3,
}
const holder = {
  plateId: 'p-old', vehicleId: 'veh-2', vehicleNumber: 'F-000002', vin: 'ZAR94000007123456', validFrom: '2024-03-01',
  isInterchangeable: false,
}

function install(onPost: (body: Record<string, unknown>) => unknown) {
  return installFakeBackend([
    { match: new RegExp(`^/vehicle-mdm/${ID}$`), handler: () => vehicle },
    { method: 'GET', match: new RegExp(`^/vehicle-mdm/${ID}/plates$`), handler: () => [current] },
    { method: 'POST', match: new RegExp(`^/vehicle-mdm/${ID}/plates$`), handler: (req) => onPost(req.body as Record<string, unknown>) },
    {
      method: 'POST',
      match: new RegExp(`^/vehicle-mdm/${ID}/plates/p-1/end$`),
      handler: (req) => ({ ...current, validTo: (req.body as { validTo: string }).validTo, version: 4 }),
    },
    { match: new RegExp(`^/vehicle-mdm/${ID}/party-roles$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/odometer-readings$`), handler: () => [] },
    { match: new RegExp(`^/vehicle-mdm/${ID}/accessories$`), handler: () => [] },
  ])
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
    </Routes>,
    { route: `/vehicles/${ID}?tab=plates` },
  )
}

async function fillThePlate(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('textbox', { name: i18n.t('vehicleDetail.plates.canton') }))
  await user.click(await screen.findByRole('option', { name: 'ZH' }))
  await user.type(within(dialog).getByRole('textbox', { name: i18n.t('vehicleDetail.plates.number') }), '123456')
  return dialog
}

afterEach(() => cleanup())

describe('Vehicle 360 › Plates — record and end a Kontrollschild (KAN-467)', () => {
  it('records a plate from the tab with its canton, number and start', async () => {
    const user = userEvent.setup()
    const backend = install((body) => ({ ...current, id: 'p-new', plate: 'ZH 123456', canton: 'ZH', ...body, version: 1 }))
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.header.recordPlate') }))
    const dialog = await fillThePlate(user)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.recordSubmit') }))

    await waitFor(() => expect(backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')
    expect(post.body).toMatchObject({ plate: '123456', canton: 'ZH', validTo: null })
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('names the other car the plate is on and moves it once the user confirms', async () => {
    const user = userEvent.setup()
    const backend = install((body) =>
      body.moveFromPlateIds
        ? { ...current, id: 'p-new', plate: 'ZH 123456', canton: 'ZH', version: 1 }
        : status(409, {
            error: {
              code: 'conflict', message: 'This plate is current on another vehicle.',
              details: { reason: 'plate_current_on_other_vehicle', holders: [holder] },
            },
          }),
    )
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.header.recordPlate') }))
    const dialog = await fillThePlate(user)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.recordSubmit') }))

    expect(
      await within(dialog).findByText(
        i18n.t('vehicleDetail.plates.onOtherVehicle', {
          plate: 'ZH 123456', vehicle: 'F-000002', vin: 'ZAR94000007123456', since: '01.03.2024',
        }),
      ),
    ).toBeInTheDocument()
    expect(within(dialog).queryByText('This plate is current on another vehicle.')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.moveConfirm') }))

    await waitFor(() => expect(backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')).toHaveLength(2))
    const [, confirmed] = backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')
    expect(confirmed.body).toMatchObject({ plate: '123456', canton: 'ZH', moveFromPlateIds: ['p-old'] })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('offers the Wechselschild for the one other car', async () => {
    const user = userEvent.setup()
    const backend = install((body) =>
      body.wechselschildWithPlateId
        ? { ...current, id: 'p-new', isInterchangeable: true, version: 1 }
        : status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'plate_current_on_other_vehicle', holders: [holder] } } }),
    )
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.header.recordPlate') }))
    const dialog = await fillThePlate(user)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.recordSubmit') }))
    await user.click(await within(dialog).findByRole('button', { name: i18n.t('vehicleDetail.plates.formWechselschild') }))

    await waitFor(() => expect(backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')).toHaveLength(2))
    expect(backend.callsTo(new RegExp(`/vehicle-mdm/${ID}/plates$`), 'POST')[1].body).toMatchObject({
      wechselschildWithPlateId: 'p-old',
    })
  })

  it('says a refused plate in the UI language', async () => {
    const user = userEvent.setup()
    install(() =>
      status(422, { error: { code: 'unprocessable_entity', message: 'A Kontrollschild is …', details: { reason: 'plate_invalid' } } }),
    )
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.header.recordPlate') }))
    const dialog = await fillThePlate(user)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.recordSubmit') }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(i18n.t('apiRefusal.reasons.plate_invalid'))
  })

  it('«Kontrollschild erfassen» is in the overflow too', async () => {
    const user = userEvent.setup()
    install(() => ({}))
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('common.moreActions') }))
    await user.click(await screen.findByRole('menuitem', { name: i18n.t('vehicleDetail.header.recordPlate') }))
    expect(await screen.findByRole('dialog', { name: i18n.t('vehicleDetail.plates.recordTitle') })).toBeInTheDocument()
  })

  it('ends a current plate under its version', async () => {
    const user = userEvent.setup()
    const backend = install(() => ({}))
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('vehicleDetail.plates.end') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('vehicleDetail.plates.end') }))

    await waitFor(() => expect(backend.callsTo(/\/plates\/p-1\/end$/, 'POST')).toHaveLength(1))
    const [end] = backend.callsTo(/\/plates\/p-1\/end$/, 'POST')
    expect(end.headers.get('If-Match')).toBe('3')
    expect(end.body).toHaveProperty('validTo')
  })
})
