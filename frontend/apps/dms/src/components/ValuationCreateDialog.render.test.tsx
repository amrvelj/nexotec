// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { nameValuationCar } from '../test/nameValuationCar'
import { installFakeBackend, status } from '../test/fakeBackend'
import { ValuationCreateDialog } from './ValuationCreateDialog'
import type { ConfigurationRead } from '../api/types'

// KAN-266 — the valuation create carries one Idempotency-Key per submission:
// a retry after a failure keeps it, a corrected valuation or another opening
// gets a new one, and a success does not renew it (the dialog stays open
// after a success only when its host failed to use the valuation).

function install({ failFirst = false } = {}) {
  let creates = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
    {
      method: 'POST',
      match: /^\/valuations$/,
      handler: () => {
        creates += 1
        if (failFirst && creates === 1) {
          return status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } })
        }
        return { __status: 201, body: { id: `val-${creates}` } }
      },
    },
  ])
}

const keys = (backend: ReturnType<typeof install>) =>
  backend.callsTo(/^\/valuations$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

async function submit(user: ReturnType<typeof userEvent.setup>, finalOffer: string) {
  const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
  const field = within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false })
  await user.clear(field)
  await user.type(field, finalOffer)
  await nameValuationCar(user, dialog)
  await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))
}

afterEach(() => cleanup())

describe('ValuationCreateDialog — one Idempotency-Key per submission (KAN-266)', () => {
  it('a create retried after a failure keeps its key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    expect(await screen.findByText(i18n.t('valuationCreate.error'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('valuationCreate.submit') }))
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, retry] = keys(backend)
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
  })

  it('a corrected valuation after a failure is a new submission, under a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    await screen.findByText(i18n.t('valuationCreate.error'))
    await submit(user, '11500')
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, corrected] = keys(backend)
    expect(first).toBeTruthy()
    expect(corrected).toBeTruthy()
    expect(corrected).not.toBe(first)
  })

  it('kept open after a success (its host failed to use the valuation), the same valuation replays it', async () => {
    // The offer's trade-in attach failed: the dialog stays open, and
    // "Erstellen" again must not create a second valuation.
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    await waitFor(() => expect(keys(backend)).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: i18n.t('valuationCreate.submit') }))
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, again] = keys(backend)
    expect(first).toBeTruthy()
    expect(again).toBe(first)
  })

  it('the same valuation after closing and reopening gets a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    function Host() {
      const [opened, setOpened] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setOpened(true)}>reopen</button>
          <ValuationCreateDialog opened={opened} onClose={() => setOpened(false)} onCreated={() => {}} />
        </>
      )
    }
    renderWithProviders(<Host />)

    await submit(user, '12000')
    await screen.findByText(i18n.t('valuationCreate.error'))
    await user.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'reopen' }))
    await submit(user, '12000')
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, reopened] = keys(backend)
    expect(first).toBeTruthy()
    expect(reopened).toBeTruthy()
    expect(reopened).not.toBe(first)
  })
})

// KAN-351 — a VIN the API refuses (422 on `vin`, KAN-331) is named at the VIN
// input in the user's language; the dialog showed the API's English text.
describe('ValuationCreateDialog — a refused VIN is named at the VIN input (KAN-351)', () => {
  it('a 422 on vin shows the translated VIN rule under the VIN input', async () => {
    await i18n.changeLanguage('fr')
    try {
      const user = userEvent.setup()
      installFakeBackend([
        { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
        {
          method: 'POST',
          match: /^\/valuations$/,
          handler: () =>
            status(422, {
              error: {
                code: 'unprocessable_entity',
                message: 'Request validation failed.',
                details: { errors: [{ loc: ['body', 'vin'], msg: 'Value error, invalid VIN', type: 'value_error' }] },
              },
            }),
        },
      ])
      renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)
      const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
      await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false }), 'WVWZZZ1KZAO331338')
      await submit(user, '12000')

      expect(await within(dialog).findByText(i18n.t('common.invalidVin'))).toBeInTheDocument()
      expect(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false })).toHaveAttribute('aria-invalid', 'true')
      expect(within(dialog).queryByText('Request validation failed.')).toBeNull()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})

// KAN-353 (Anto, option C) — a car first registered before 1981 may carry a
// chassis number in the VIN field: the dialog sends the first registration
// with it, and a chassis number the API refuses for want of that date is
// said in the user's language.
describe('ValuationCreateDialog — a pre-1981 chassis number (KAN-353)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  function installChassis() {
    return installFakeBackend([
      { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
      {
        method: 'POST',
        match: /^\/valuations$/,
        handler: (req) => {
          const body = req.body as { vehicleFirstRegistration?: string | null }
          if (!body.vehicleFirstRegistration || body.vehicleFirstRegistration >= '1981-01-01') {
            return status(422, {
              error: {
                code: 'unprocessable_entity',
                message: 'Not a valid VIN. A chassis number ...',
                details: { field: 'vin', reason: 'chassis_number_requires_pre_1981_registration' },
              },
            })
          }
          return { __status: 201, body: { id: 'val-1' } }
        },
      },
    ])
  }

  it('sends the first registration with the chassis number, in fr', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const backend = installChassis()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false }), '9112300123')
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.firstRegistration')), '1972-05-01')
    await submit(user, '85000')

    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/valuations$/, 'POST')[0].body).toMatchObject({
      vin: '9112300123',
      vehicleFirstRegistration: '1972-05-01',
    })
  })

  it('names the missing pre-1981 first registration at the VIN field, in fr', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installChassis()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false }), '9112300123')
    await submit(user, '85000')

    expect(await screen.findByText(i18n.t('common.chassisNumberNeedsPre1981'))).toBeInTheDocument()
    expect(screen.queryByText('Not a valid VIN. A chassis number ...')).toBeNull()
  })
})

// KAN-449 (Anto, 2026-10-25) — a configuration that names a car (a VIN or a
// linked record) gives the valuation its VIN: the dialog shows it read-only
// and sends no VIN of its own beside it, so the valuation cannot name one car
// by VIN and another by id. A configuration with no VIN links no car, so the
// VIN stays free to type (Anto, 2026-10-26, option A).
describe("ValuationCreateDialog — the VIN of a configuration's valuation (KAN-449)", () => {
  const configuration = {
    id: 'cfg-1', tenantId: 't1', source: 'manual', mode: 'record', catalogueMatchStatus: 'unmatched',
    matchMethod: 'kontrollschild', catalogueVariantId: null, catalogueVariantLabel: null,
    vehicleId: 'veh-1', vehicleLabel: null, vin: 'WVWZZZ1KZAW449001', stammnummer: null,
    typeApprovalNumber: null, firstRegistrationDate: '2019-04-01', licencePlate: null, mileageKm: 42000,
    brandDisplayName: 'Volkswagen', modelGroupName: 'Golf', variantName: 'Golf GTI', vehicleKind: null,
    fuelType: null, bodyStyle: null, drivetrain: null, transmission: null, exteriorColour: null,
    interiorColour: null, exteriorColourSurcharge: null, interiorColourSurcharge: null, wheels: null,
    wheelsSurcharge: null, spec: {}, overriddenFields: [], options: [], notes: null, version: 1,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  } as unknown as ConfigurationRead

  const vinField = (dialog: HTMLElement) =>
    within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('valuationCreate.vin')) })

  it("shows the configuration's VIN read-only and sends none beside the configuration", async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(
      <ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} configuration={configuration} />,
    )

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(vinField(dialog)).toHaveValue('WVWZZZ1KZAW449001')
    expect(vinField(dialog)).toHaveAttribute('readonly')
    await user.type(vinField(dialog), 'X')
    expect(vinField(dialog)).toHaveValue('WVWZZZ1KZAW449001')
    await submit(user, '18000')

    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/valuations$/, 'POST')[0].body).toMatchObject({ vin: null, configurationId: 'cfg-1' })
  })

  it('a configuration linked to a car without a stored VIN keeps the VIN read-only and cannot be valued', async () => {
    // KAN-574 (Anto, 2026-11-07, option A): a valuation names its car by its
    // VIN; this shape (no writer leaves it today) has none to give, so the
    // configuration's VIN is repaired first — the submit stays disabled.
    const user = userEvent.setup()
    const backend = install()
    const linkedOnly = { ...configuration, vin: null, vehicleId: 'veh-1' } as ConfigurationRead
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} configuration={linkedOnly} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(vinField(dialog)).toHaveAttribute('readonly')
    const field = within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false })
    await user.type(field, '18000')

    expect(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') })).toBeDisabled()
    expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(0)
  })

  it('the submit waits for the car: VIN, make and model (KAN-574)', async () => {
    const user = userEvent.setup()
    install()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    const button = () => within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') })
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false }), '9000')
    expect(button()).toBeDisabled()
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false }), 'WVWZZZ1KZAW574098')
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.make'), { exact: false }), 'VW')
    expect(button()).toBeDisabled()
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.model'), { exact: false }), 'Golf')
    expect(button()).toBeEnabled()
  })

  it('a configuration with no VIN links no car, so the VIN stays free to type and is sent', async () => {
    const user = userEvent.setup()
    const backend = install()
    const plateOnly = { ...configuration, vin: null, vehicleId: null } as ConfigurationRead
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} configuration={plateOnly} />)

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(vinField(dialog)).not.toHaveAttribute('readonly')
    await user.type(vinField(dialog), 'wvwzzz1kzaw449002')
    await submit(user, '18000')

    await waitFor(() => expect(backend.callsTo(/^\/valuations$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/valuations$/, 'POST')[0].body).toMatchObject({
      vin: 'WVWZZZ1KZAW449002',
      configurationId: 'cfg-1',
    })
  })

  it('without a configuration the VIN stays free to type', async () => {
    install()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)
    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(vinField(dialog)).not.toHaveAttribute('readonly')
  })

  it("names a VIN the API refuses as not the configuration's in the UI language, in it", async () => {
    await i18n.changeLanguage('it')
    try {
      const user = userEvent.setup()
      installFakeBackend([
        { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
        {
          method: 'POST',
          match: /^\/valuations$/,
          handler: () =>
            status(422, {
              error: {
                code: 'unprocessable_entity',
                message: "The VIN differs from the configuration's.",
                details: { field: 'vin', reason: 'vin_differs_from_configuration' },
              },
            }),
        },
      ])
      renderWithProviders(
        <ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} configuration={configuration} />,
      )
      await submit(user, '18000')

      expect(await screen.findByText(i18n.t('valuationCreate.vinDiffersFromConfiguration'))).toBeInTheDocument()
      expect(screen.queryByText("The VIN differs from the configuration's.")).toBeNull()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
