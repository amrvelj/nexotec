// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { GOLF_CANDIDATE, catalogueVariant, configurationRead, identification } from '../../test/configuratorFixtures'
import { IdentificationPanel } from './IdentificationPanel'

// C-D (KAN-42) — FR-C-02 on screen: one input, the FR-V-06 picker on every
// ambiguous answer, nothing selected for the advisor, a best match only
// applied on confirmation, and no trace of a missing VIN entitlement.

const WECHSELSCHILD = identification({
  kind: 'kontrollschild',
  matchMethod: 'kontrollschild',
  outcome: 'plate_records',
  observed: { ...identification().observed, licencePlate: 'ZH999999' },
  plateRecords: [
    {
      vehicleKindCode: '01', brandName: 'Alfa Romeo', modelDescription: 'Giulietta 1.4 TB', productionFrom: 2010,
      productionTo: 2013, typeApprovalNumber: '1AB234', firstRegistrationDate: '2011-05-12', stammnummer: '111222333',
    },
    {
      vehicleKindCode: '01', brandName: 'Volkswagen', modelDescription: 'Golf GTI', productionFrom: 2021,
      productionTo: null, typeApprovalNumber: '2CD456', firstRegistrationDate: '2022-03-01', stammnummer: '444555666',
    },
  ],
  plateRecordsInterchangeable: true,
})

const ONE_VARIANT = identification({
  kind: 'typenschein',
  matchMethod: 'typenschein',
  outcome: 'variants',
  observed: { ...identification().observed, typeApprovalNumber: '2CD456' },
  variants: [GOLF_CANDIDATE],
})

function setup(routes: FakeRoute[], allowedModes: ('build' | 'record')[] = ['build', 'record']) {
  const backend = installFakeBackend([
    { method: 'GET', match: /^\/catalogue\/variants\/v1$/, handler: () => catalogueVariant() },
    ...routes,
  ])
  const onStart = vi.fn()
  const onReuse = vi.fn()
  renderWithProviders(<IdentificationPanel allowedModes={allowedModes} onStart={onStart} onReuse={onReuse} />)
  return { backend, onStart, onReuse }
}

async function identify(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText(i18n.t('configurator.find.idLabel'), { exact: false }), text)
  await user.click(screen.getByRole('button', { name: i18n.t('configurator.identify.submit') }))
}

describe('IdentificationPanel', () => {
  it('sends every kind of input through the same single field, without asking which it is', async () => {
    const user = userEvent.setup()
    const { backend } = setup([
      { method: 'GET', match: /^\/vehicle-identification$/, handler: () => identification({ kind: 'kontrollschild' }) },
    ])

    await identify(user, 'ZH 999 999')
    await screen.findByText(i18n.t('configurator.identify.recognisedAs', { kind: i18n.t('configurator.identify.kind.kontrollschild') }))

    const calls = backend.calls.filter((c) => c.pathname === '/vehicle-identification')
    expect(calls).toHaveLength(1)
    expect(calls[0].params.get('q')).toBe('ZH 999 999')
  })

  it('shows a Wechselschild as a legitimate picker and selects nothing by itself', async () => {
    const user = userEvent.setup()
    const { backend, onStart } = setup([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: (req) => (req.params.get('q') === '2CD456' ? ONE_VARIANT : WECHSELSCHILD),
      },
    ])

    await identify(user, 'ZH999999')
    const picker = await screen.findByText(i18n.t('configurator.identify.plateRecords.wechselschild'))
    expect(screen.queryByText(i18n.t('configurator.identify.plateRecords.conflict'))).not.toBeInTheDocument()
    expect(screen.getByText('Alfa Romeo Giulietta 1.4 TB')).toBeInTheDocument()
    expect(screen.getByText('Volkswagen Golf GTI')).toBeInTheDocument()
    expect(onStart).not.toHaveBeenCalled()
    expect(backend.calls.some((c) => c.method === 'POST')).toBe(false)
    expect(picker).toBeInTheDocument()

    // Picking the Golf resolves its Typenschein; even one variant is a row
    // the advisor picks.
    await user.click(screen.getByText('Volkswagen Golf GTI'))
    await screen.findByText(i18n.t('configurator.identify.variantTitle'))
    expect(onStart).not.toHaveBeenCalled()

    await user.click(screen.getByText('Volkswagen Golf Golf GTI'))
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1))
    const start = onStart.mock.calls[0][0]
    expect(start.matchMethod).toBe('kontrollschild')
    expect(start.observed).toMatchObject({
      licencePlate: 'ZH999999',
      stammnummer: '444555666',
      firstRegistrationDate: '2022-03-01',
      typeApprovalNumber: '2CD456',
    })
    expect(start.confirmedBestMatchCode).toBeUndefined()
  })

  it('titles a genuine plate conflict as a conflict, not as a Wechselschild', async () => {
    const user = userEvent.setup()
    setup([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: () => ({ ...WECHSELSCHILD, plateRecordsInterchangeable: false, plateRecordsConflict: true }),
      },
    ])

    await identify(user, 'GE111111')
    expect(await screen.findByText(i18n.t('configurator.identify.plateRecords.conflict'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('configurator.identify.plateRecords.wechselschild'))).not.toBeInTheDocument()
  })

  it('applies a best match only when the advisor confirms it', async () => {
    const user = userEvent.setup()
    const { backend, onStart } = setup([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: () =>
          identification({
            outcome: 'variants',
            observed: { ...identification().observed, typeApprovalNumber: '9ZZ001' },
            variants: [GOLF_CANDIDATE, { ...GOLF_CANDIDATE, catalogueVariantId: 'v2', variantName: 'Golf R' }],
            bestMatchAvailable: true,
          }),
      },
      {
        method: 'GET',
        match: /^\/vehicle-identification\/best-match$/,
        handler: () => ({
          candidate: GOLF_CANDIDATE,
          matchCode: 2,
          newPrice: '48900',
          newPriceSource: 'document',
          fields: [
            { field: 'typeApprovalNumber', entered: '9ZZ001', matched: '9ZZ001', agrees: true },
            { field: 'newPrice', entered: '48900', matched: '42500.00', agrees: false },
          ],
          requiresConfirmation: true,
        }),
      },
    ])

    await identify(user, '9ZZ001')
    await screen.findByText(i18n.t('configurator.identify.variantsTitle'))
    const bestMatch = screen.getByTestId('best-match')
    await user.type(within(bestMatch).getByLabelText(i18n.t('configurator.bestMatch.newPrice')), '48900')
    await user.click(within(bestMatch).getByRole('textbox', { name: i18n.t('configurator.bestMatch.source.label') }))
    await user.click(await screen.findByText(i18n.t('configurator.bestMatch.source.document')))
    await user.click(within(bestMatch).getByRole('button', { name: i18n.t('configurator.bestMatch.ask') }))

    const proposal = await screen.findByTestId('best-match-proposal')
    expect(within(proposal).getByText(i18n.t('configurator.bestMatch.matchCode.2'))).toBeInTheDocument()
    expect(within(proposal).getByText(i18n.t('configurator.bestMatch.differs'))).toBeInTheDocument()
    const asked = backend.calls.find((c) => c.pathname === '/vehicle-identification/best-match')!
    expect(asked.params.get('newPriceSource')).toBe('document')
    expect(onStart).not.toHaveBeenCalled()

    await user.click(within(proposal).getByRole('button', { name: i18n.t('configurator.bestMatch.confirm') }))
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1))
    expect(onStart.mock.calls[0][0].confirmedBestMatchCode).toBe(2)
  })

  it('says nothing at all about a VIN decode the dealer is not entitled to', async () => {
    const user = userEvent.setup()
    setup([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: () =>
          identification({
            kind: 'vin',
            matchMethod: 'vin',
            observed: { ...identification().observed, vin: 'WVWZZZ1KZAW000099' },
            notes: ['vin_decode_not_entitled'],
          }),
      },
    ])

    await identify(user, 'WVWZZZ1KZAW000099')
    await screen.findByText(i18n.t('configurator.identify.none'))
    expect(screen.getByTestId('identification-result').textContent).not.toMatch(/vin_decode|VIN-Entschl|decod/i)
  })

  it('refuses to reuse a configuration in a mode this host does not allow', async () => {
    const user = userEvent.setup()
    const { onReuse } = setup(
      [
        {
          method: 'GET',
          match: /^\/vehicle-identification$/,
          handler: () =>
            identification({
              kind: 'vin',
              matchMethod: 'vin',
              outcome: 'existing_vehicle',
              existingVehicle: {
                vehicleId: 'veh-1', vehicleNumber: 'F-000042', vin: 'WVWZZZ1KZAW000002',
                catalogueVariantId: null, reusableConfigurationId: 'cfg-9',
              },
            }),
        },
        { method: 'GET', match: /^\/configurations\/cfg-9$/, handler: () => configurationRead({ id: 'cfg-9', mode: 'record' }) },
      ],
      ['build'],
    )

    await identify(user, 'WVWZZZ1KZAW000002')
    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.identify.existingVehicle.reuse') }))

    expect(
      await screen.findByText(i18n.t('configurator.identify.reuseModeMismatch', { mode: i18n.t('configurator.mode.record') })),
    ).toBeInTheDocument()
    expect(onReuse).not.toHaveBeenCalled()
  })

  it('names an unrecognisable input instead of guessing', async () => {
    const user = userEvent.setup()
    setup([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: () => status(422, { error: { code: 'unprocessable_entity', message: 'x', details: { reason: 'unrecognised_identifier' } } }),
      },
    ])

    await identify(user, '§§§')
    expect(await screen.findByText(i18n.t('configurator.identify.unrecognised'))).toBeInTheDocument()
  })
})
