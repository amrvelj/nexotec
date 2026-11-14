// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { StockItemRead } from '../../api/types'
import { VinCorrectionDialog } from './VinCorrectionDialog'

// KAN-557 (ADR-116) — «VIN korrigieren»: one POST corrects the stock item,
// its vehicle-mdm record and its configuration together; the dialog says so,
// asks for the new VIN and a reason, and says a refusal in the UI language.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const OLD_VIN = 'TMBJJ7NX5MY012345'
const NEW_VIN = 'TMBJJ7NX5MY012346'
const ITEM = {
  id: 'st-557',
  version: 4,
  stockNumber: 'S-000557',
  vin: OLD_VIN,
  vehicleId: 'v-557',
  configurationId: 'cfg-557',
} satisfies Pick<StockItemRead, 'id' | 'version' | 'stockNumber' | 'vin' | 'vehicleId' | 'configurationId'>

const PATH = /^\/inventory\/stock-items\/st-557\/vin-correction$/
const vinField = () => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.newVinLabel')) })
const reasonField = () => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.reasonLabel')) })
const submit = () => screen.getByRole('button', { name: i18n.t('stockDetail.vinCorrection.submit') })

function renderDialog(onCorrected = vi.fn(), onClose = vi.fn(), queryClient?: QueryClient) {
  renderWithProviders(<VinCorrectionDialog opened item={ITEM} onClose={onClose} onCorrected={onCorrected} />, { queryClient })
  return { onCorrected, onClose }
}

describe('VinCorrectionDialog (KAN-557)', () => {
  it('names the three records it changes, with the stock number', async () => {
    installFakeBackend([])
    renderDialog()
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText(i18n.t('stockDetail.vinCorrection.body', { stockNumber: 'S-000557' })),
    ).toHaveTextContent('Die VIN wird auf dem Lagerfahrzeug S-000557, im Fahrzeugstamm und in der Konfiguration geändert.')
    expect(within(dialog).getByText(OLD_VIN)).toBeInTheDocument()
  })

  it('requires both fields, and refuses an invalid VIN in place — nothing is sent', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([])
    renderDialog()
    await screen.findByRole('dialog')

    await user.click(submit())
    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.newVinRequired'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.vinCorrection.reasonRequired'))).toBeInTheDocument()

    await user.type(vinField(), 'TMBJJ7NX5MY01234O') // O for 0
    await user.type(reasonField(), '   ')
    await user.click(submit())
    expect(await screen.findByText(i18n.t('common.invalidVin'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('stockDetail.vinCorrection.reasonRequired'))).toBeInTheDocument()

    await user.clear(vinField())
    await user.type(vinField(), OLD_VIN.toLowerCase())
    await user.clear(reasonField())
    await user.type(reasonField(), 'Tippfehler')
    await user.click(submit())
    expect(await screen.findByText(i18n.t('apiRefusal.reasons.vin_unchanged'))).toBeInTheDocument()

    expect(backend.callsTo(PATH, 'POST')).toHaveLength(0)
  })

  it('POSTs the normalised VIN and the reason with If-Match and an Idempotency-Key, then refreshes the caches', async () => {
    const user = userEvent.setup()
    const updated = { ...ITEM, vin: NEW_VIN, version: 5 }
    const backend = installFakeBackend([{ method: 'POST', match: PATH, handler: () => updated }])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { onCorrected, onClose } = renderDialog(vi.fn(), vi.fn(), queryClient)
    await screen.findByRole('dialog')

    await user.type(vinField(), ' tmbjj7nx5my 012346 ')
    await user.type(reasonField(), '  Tippfehler beim Einkauf  ')
    await user.click(submit())

    await waitFor(() => expect(backend.callsTo(PATH, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(PATH, 'POST')
    expect(call.body).toEqual({ newVin: NEW_VIN, reason: 'Tippfehler beim Einkauf' })
    expect(call.headers.get('If-Match')).toBe('4')
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)

    await waitFor(() => expect(onCorrected).toHaveBeenCalledWith(updated))
    expect(onClose).toHaveBeenCalled()
    expect(queryClient.getQueryData(['stock-item', 'st-557'])).toEqual(updated)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['configuration', 'cfg-557'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['vehicle-mdm', 'v-557'] })
  })

  it('says a refusal in the UI language — another record carries the VIN, named by its number', async () => {
    const user = userEvent.setup()
    await i18n.changeLanguage('fr')
    installFakeBackend([
      {
        method: 'POST',
        match: PATH,
        handler: () => ({
          __status: 409,
          body: {
            error: {
              code: 'conflict',
              message: 'Another vehicle-mdm record carries this VIN.',
              details: { reason: 'vin_belongs_to_other_vehicle', vehicleNumber: 'F-000042' },
            },
          },
        }),
      },
    ])
    const { onCorrected, onClose } = renderDialog()
    await screen.findByRole('dialog')

    await user.type(vinField(), NEW_VIN)
    await user.type(reasonField(), 'Faute de frappe')
    await user.click(submit())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      // KAN-524: since Anto's Q1 B the dealer merges the two records themselves.
      "Ce VIN est déjà celui du véhicule F-000042 dans la fiche véhicule. S'il s'agit du même véhicule, fusionnez les deux depuis la fiche avec «Fusionner avec…».",
    )
    expect(screen.queryByText(/Another vehicle-mdm record/)).toBeNull()
    expect(onCorrected).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('refuses a chassis number on a car bought under a VIN — nothing is sent', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([])
    renderDialog()
    await screen.findByRole('dialog')

    await user.type(vinField(), '1130101234568')
    await user.type(reasonField(), 'Tippfehler')
    await user.click(submit())

    expect(await screen.findByText(i18n.t('common.invalidVin'))).toBeInTheDocument()
    expect(backend.callsTo(PATH, 'POST')).toHaveLength(0)
  })
})

// KAN-535 (Anto, 2026-11-14, option B): a pre-1981 car bought under its
// chassis number has that number corrected through the same dialog.
describe('VinCorrectionDialog on a car bought under its chassis number (KAN-535)', () => {
  const CHASSIS = '1130101234567'
  const OLDTIMER = { ...ITEM, vin: CHASSIS }
  const chassisField = () =>
    screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('stockDetail.vinCorrection.newChassisNumberLabel')) })

  function renderOldtimer() {
    renderWithProviders(<VinCorrectionDialog opened item={OLDTIMER} onClose={vi.fn()} onCorrected={vi.fn()} />)
  }

  it('names the chassis number it changes', async () => {
    installFakeBackend([])
    renderOldtimer()
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText(i18n.t('stockDetail.vinCorrection.chassisBody', { stockNumber: 'S-000557' })),
    ).toHaveTextContent('Die Chassisnummer wird auf dem Lagerfahrzeug S-000557, im Fahrzeugstamm und in der Konfiguration geändert.')
    expect(within(dialog).getByText(i18n.t('stockDetail.vinCorrection.currentChassisNumber'))).toBeInTheDocument()
    expect(within(dialog).getByText(CHASSIS)).toBeInTheDocument()
  })

  it('refuses a value that is no chassis number, and the unchanged one, in place — nothing is sent', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([])
    renderOldtimer()
    await screen.findByRole('dialog')

    await user.click(submit())
    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.newChassisNumberRequired'))).toBeInTheDocument()

    await user.type(chassisField(), '113-010')
    await user.type(reasonField(), 'Tippfehler')
    await user.click(submit())
    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.invalidChassisNumber'))).toBeInTheDocument()

    await user.clear(chassisField())
    await user.type(chassisField(), ` ${CHASSIS} `)
    await user.click(submit())
    expect(await screen.findByText(i18n.t('stockDetail.vinCorrection.chassisNumberUnchanged'))).toBeInTheDocument()

    expect(backend.callsTo(PATH, 'POST')).toHaveLength(0)
  })

  it.each([
    ['another chassis number', ' 113010 1234568 ', '1130101234568'],
    ['its real VIN', 'wdb1130421a000001', 'WDB1130421A000001'],
  ])('POSTs %s in the one spelling', async (_case, typed, sent) => {
    const user = userEvent.setup()
    const backend = installFakeBackend([{ method: 'POST', match: PATH, handler: () => ({ ...OLDTIMER, vin: sent, version: 5 }) }])
    renderOldtimer()
    await screen.findByRole('dialog')

    await user.type(chassisField(), typed)
    await user.type(reasonField(), 'Fahrgestellnummer falsch abgeschrieben')
    await user.click(submit())

    await waitFor(() => expect(backend.callsTo(PATH, 'POST')).toHaveLength(1))
    expect(backend.callsTo(PATH, 'POST')[0].body).toEqual({ newVin: sent, reason: 'Fahrgestellnummer falsch abgeschrieben' })
  })
})
