// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRequest } from '../test/fakeBackend'
import type { SalesContractRead } from '../api/types'
import { ContractDetailPage } from './ContractDetailPage'

// KAN-523 — a confirmed contract's primary action is «Rechnung anfordern»,
// which asks for the plate the car leaves with (or «ohne Kontrollschild»,
// Anto 2026-11-01) and, for a plate on another car of the group, asks before
// moving it (as Vehicle 360 does).

const CONFIRMED = {
  id: 'k1',
  contractNumber: 'C-000001',
  offerNumber: 'O-000001',
  status: 'confirmed',
  version: 4,
  customerId: 'cust-1',
  customerLabel: 'Ursula Vogt',
  vehicleSource: 'stock',
  vehicleLabel: 'Seat Leon',
  grossPrice: '32000.00',
  tradeInValue: null,
  payable: null,
  margin: null,
  financing: null,
  isInvoiceable: true,
  isLegacy: false,
} as unknown as SalesContractRead

const HOLDER = {
  plateId: 'p-old', vehicleId: 'v-old', vehicleNumber: 'F-000002', vin: 'ZAR94000007123456',
  validFrom: '2024-03-01', isInterchangeable: false, version: 2,
}

function install(contract: SalesContractRead, onRequest: (req: FakeRequest) => unknown = () => contract) {
  return installFakeBackend([
    { match: /^\/sales\/contracts\/k1$/, handler: () => contract },
    { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'POST', match: /^\/sales\/contracts\/k1\/request-invoice$/, handler: onRequest },
  ])
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
    </Routes>,
    { route: '/sales/contracts/k1' },
  )
}

async function openTheDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.requestInvoice') }))
  return screen.findByRole('dialog')
}

async function typeThePlate(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.click(within(dialog).getByRole('radio', { name: i18n.t('contractDetail.requestInvoice.withPlate') }))
  await user.click(within(dialog).getByRole('textbox', { name: i18n.t('vehicleDetail.plates.canton') }))
  await user.click(await screen.findByRole('option', { name: 'ZH' }))
  await user.type(within(dialog).getByRole('textbox', { name: i18n.t('vehicleDetail.plates.number') }), '123456')
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('ContractDetailPage › «Rechnung anfordern» with the plate (KAN-523)', () => {
  it('sends the plate with its canton under If-Match and says it is being recorded', async () => {
    const user = userEvent.setup()
    const backend = install(CONFIRMED)
    renderPage()

    const dialog = await openTheDialog(user)
    const submit = within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.submit') })
    expect(submit).toBeDisabled() // a choice is required: a plate, or «ohne Kontrollschild»
    await typeThePlate(user, dialog)
    await user.click(submit)

    const [post] = backend.callsTo(/\/request-invoice$/, 'POST')
    expect(post!.body).toEqual({ plate: '123456', canton: 'ZH' })
    expect(post!.headers.get('If-Match')).toBe('4')
    expect(post!.headers.get('Idempotency-Key')).toBeTruthy()
    expect(
      await screen.findByText(i18n.t('contractDetail.requestInvoice.requestedWithPlate', { plate: 'ZH 123456' })),
    ).toBeInTheDocument()
  })

  it('«ohne Kontrollschild» sends no plate', async () => {
    const user = userEvent.setup()
    const backend = install(CONFIRMED)
    renderPage()

    const dialog = await openTheDialog(user)
    await user.click(within(dialog).getByRole('radio', { name: i18n.t('contractDetail.requestInvoice.withoutPlate') }))
    expect(within(dialog).queryByRole('textbox', { name: i18n.t('vehicleDetail.plates.number') })).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.submit') }))

    const [post] = backend.callsTo(/\/request-invoice$/, 'POST')
    expect(post!.body ?? null).toBeNull()
    expect(await screen.findByText(i18n.t('contractDetail.requestInvoice.requested'))).toBeInTheDocument()
  })

  it('a plate on another car of the group is named, and the confirmed move is sent with its version', async () => {
    const user = userEvent.setup()
    const backend = install(CONFIRMED, (req) =>
      (req.body as { moveFrom?: unknown }).moveFrom
        ? CONFIRMED
        : status(409, {
            error: {
              code: 'conflict',
              message: 'This plate is current on another vehicle; confirm the move or form a Wechselschild.',
              details: { reason: 'plate_current_on_other_vehicle', holders: [HOLDER] },
            },
          }),
    )
    renderPage()

    const dialog = await openTheDialog(user)
    await typeThePlate(user, dialog)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.submit') }))

    expect(
      await within(dialog).findByText(
        i18n.t('vehicleDetail.plates.onOtherVehicle', {
          plate: 'ZH 123456', vehicle: 'F-000002', vin: 'ZAR94000007123456', since: '01.03.2024',
        }),
      ),
    ).toBeInTheDocument()
    // No Wechselschild here: a sold car leaves with its own plate.
    expect(within(dialog).queryByRole('button', { name: i18n.t('vehicleDetail.plates.formWechselschild') })).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.moveSubmit') }))

    await waitFor(() => expect(backend.callsTo(/\/request-invoice$/, 'POST')).toHaveLength(2))
    expect(backend.callsTo(/\/request-invoice$/, 'POST')[1]!.body).toEqual({
      plate: '123456', canton: 'ZH', moveFrom: [{ plateId: 'p-old', version: 2 }],
    })
  })

  it('a refusal is said in the UI language', async () => {
    const user = userEvent.setup()
    install(CONFIRMED, () =>
      status(422, { error: { code: 'unprocessable', message: 'A Kontrollschild is the canton and one to six digits.', details: { reason: 'plate_invalid' } } }),
    )
    renderPage()

    const dialog = await openTheDialog(user)
    await typeThePlate(user, dialog)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.submit') }))

    expect(await within(dialog).findByText(i18n.t('apiRefusal.reasons.plate_invalid'))).toBeInTheDocument()
  })

  it('a plate the other car holds only from after the delivery date says what to do here', async () => {
    const user = userEvent.setup()
    install(CONFIRMED, () =>
      status(409, { error: { code: 'conflict', message: "The other vehicle's assignment starts on or after this one.", details: { reason: 'plate_move_before_start', holders: [HOLDER] } } }),
    )
    renderPage()

    const dialog = await openTheDialog(user)
    await typeThePlate(user, dialog)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('contractDetail.requestInvoice.submit') }))

    expect(await within(dialog).findByText(i18n.t('contractDetail.requestInvoice.moveBeforeStart'))).toBeInTheDocument()
  })

  it('a car not yet purchased cannot be invoiced, and the action says why', async () => {
    install({ ...CONFIRMED, isInvoiceable: false })
    renderPage()

    const button = await screen.findByRole('button', { name: i18n.t('contractDetail.actions.requestInvoice') })
    expect(button).toBeDisabled()
  })

  it('a pending contract keeps «Vertrag bestätigen» as its primary action', async () => {
    install({ ...CONFIRMED, status: 'pending' } as unknown as SalesContractRead)
    renderPage()

    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('contractDetail.actions.requestInvoice') })).not.toBeInTheDocument()
  })
})
