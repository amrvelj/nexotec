// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import en from '../../i18n/locales/en'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import type { VehicleMdmDetailRead } from '../../api/types'
import { MergeVehicleDialog, mergeCandidates } from './MergeVehicleDialog'

// KAN-524 (FR-V-12) — «Zusammenführen mit…»: two records of one car become
// one for good. The dialog compares both, says what follows, and unlocks the
// red button only once the vehicle number of the record that goes is typed.
// The POST carries If-Match (the duplicate), survivorVersion and an
// Idempotency-Key; every refusal is said in the user's language.

const MARKER = /⚠ MISSING I18N KEY/

function record(overrides: Partial<VehicleMdmDetailRead>): VehicleMdmDetailRead {
  return {
    id: 'veh-1',
    vin: 'WVWZZZ1KZAW524101',
    vehicleNumber: 'F-000101',
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: '2019-03-14',
    catalogueVariantId: null,
    catalogueMatchStatus: 'unmatched',
    vehicleStatus: 'active',
    mergedIntoVehicleId: null,
    version: 3,
    createdAt: '2026-11-01T10:00:00Z',
    updatedAt: '2026-11-01T10:00:00Z',
    stockItem: null,
    ...overrides,
  } as VehicleMdmDetailRead
}

const OPEN = record({})
const OTHER = record({ id: 'veh-2', vin: 'WVWZZZ1KZAW524100', vehicleNumber: 'F-000100', version: 7, stockItem: { id: 's-1', stockNumber: 'L-0042' } })

function install({ refuse }: { refuse?: Record<string, unknown> } = {}) {
  return installFakeBackend([
    {
      method: 'GET',
      match: /^\/vehicle-mdm\/search$/,
      handler: () => ({
        resolved: null,
        pickerCandidates: [],
        filtered: { items: [OPEN, OTHER], nextCursor: null, total: 2, totalIsEstimate: false },
      }),
    },
    { method: 'GET', match: /^\/vehicle-mdm\/veh-2$/, handler: () => OTHER },
    {
      method: 'POST',
      match: /^\/vehicle-mdm\/veh-[12]\/merge$/,
      handler: () => (refuse ? status(409, { error: { code: 'conflict', message: 'x', details: refuse } }) : OTHER),
    },
  ])
}

async function pickOther(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText(i18n.t('vehicleDetail.mergeDialog.searchPlaceholder')), 'WVWZZZ')
  await user.click(await screen.findByText(OTHER.vin))
  await screen.findByText(i18n.t('vehicleDetail.mergeDialog.irreversibleTitle'))
}

const submitButton = () => screen.getByRole('button', { name: i18n.t('vehicleDetail.mergeDialog.submit') })

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('MergeVehicleDialog — the merge (KAN-524)', () => {
  it('the button stays locked until the vehicle number of the record that goes is typed; the POST names both versions', async () => {
    const user = userEvent.setup()
    const onMerged = vi.fn()
    const backend = install()
    renderWithProviders(<MergeVehicleDialog opened onClose={() => {}} vehicle={OPEN} onMerged={onMerged} />)

    await pickOther(user)
    // The open record is not offered as its own partner; both are compared.
    expect(screen.getByText(i18n.t('vehicleDetail.mergeDialog.goes'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('vehicleDetail.mergeDialog.stays'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('vehicleDetail.mergeDialog.inYourStock', { stockNumber: 'L-0042' }))).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()

    // The record that goes is the open one, F-000101: the survivor's number does not unlock it.
    const confirm = screen.getByLabelText(i18n.t('vehicleDetail.mergeDialog.typeToConfirm', { vehicleNumber: 'F-000101' }))
    await user.type(confirm, 'F-000100')
    expect(submitButton()).toBeDisabled()
    await user.clear(confirm)
    await user.type(confirm, 'f-000101')
    expect(submitButton()).toBeEnabled()
    await user.click(submitButton())

    await waitFor(() => expect(onMerged).toHaveBeenCalledWith('veh-2'))
    const [call] = backend.callsTo(/^\/vehicle-mdm\/veh-1\/merge$/, 'POST')
    expect(call.body).toEqual({ survivorId: 'veh-2', survivorVersion: 7 })
    expect(call.headers.get('If-Match')).toBe('3')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('«Richtung tauschen» keeps the open record and folds the one found into it', async () => {
    const user = userEvent.setup()
    const onMerged = vi.fn()
    const backend = install()
    renderWithProviders(<MergeVehicleDialog opened onClose={() => {}} vehicle={OPEN} onMerged={onMerged} />)

    await pickOther(user)
    await user.click(screen.getByText(i18n.t('vehicleDetail.mergeDialog.swap')))
    await user.type(screen.getByLabelText(i18n.t('vehicleDetail.mergeDialog.typeToConfirm', { vehicleNumber: 'F-000100' })), 'F-000100')
    await user.click(submitButton())

    await waitFor(() => expect(onMerged).toHaveBeenCalledWith('veh-1'))
    const [call] = backend.callsTo(/^\/vehicle-mdm\/veh-2\/merge$/, 'POST')
    expect(call.body).toEqual({ survivorId: 'veh-1', survivorVersion: 3 })
    expect(call.headers.get('If-Match')).toBe('7')
  })

  it('a refusal is said in the user’s language, naming both stock cars', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    install({ refuse: { reason: 'vehicle_merge_both_in_stock', duplicateStockNumber: 'L-0041', survivorStockNumber: 'L-0042' } })
    renderWithProviders(<MergeVehicleDialog opened onClose={() => {}} vehicle={OPEN} onMerged={() => {}} />)

    await pickOther(user)
    await user.type(screen.getByLabelText(i18n.t('vehicleDetail.mergeDialog.typeToConfirm', { vehicleNumber: 'F-000101' })), 'F-000101')
    await user.click(submitButton())

    const shown = await screen.findByRole('alert')
    expect(shown.textContent).toBe(i18n.t('vehicleDetail.mergeDialog.refused.bothInStock', { duplicate: 'L-0041', survivor: 'L-0042' }))
    expect(shown.textContent).toContain('L-0041')
  })
})

describe('MergeVehicleDialog — every string is translated', () => {
  const english = Object.values(en.vehicleDetail.mergeDialog).filter((v): v is string => typeof v === 'string')
  for (const lng of ['de', 'fr', 'it'] as const) {
    it(`renders both steps in ${lng} with no English and no missing-key marker`, async () => {
      await i18n.changeLanguage(lng)
      const user = userEvent.setup()
      install()
      renderWithProviders(<MergeVehicleDialog opened onClose={() => {}} vehicle={OPEN} onMerged={() => {}} />)

      await pickOther(user)
      const text = screen.getByRole('dialog').textContent ?? ''
      expect(text).not.toMatch(MARKER)
      for (const phrase of english) {
        if (phrase.includes('{{')) continue
        expect(text.includes(phrase), `"${phrase}" leaked in ${lng}`).toBe(false)
      }
    })
  }
})

describe('mergeCandidates', () => {
  it('joins the hit, the picker and the page, without the open record or a repeat', () => {
    const candidates = mergeCandidates(
      {
        resolved: { ...OTHER, currentPlate: null, currentPlateWithheld: false },
        pickerCandidates: [{ id: 'veh-3', vin: 'V3', vehicleNumber: 'F-3', plate: null, plateGroupId: null, isConflict: false }],
        filtered: { items: [OPEN, OTHER], nextCursor: null, total: 2, totalIsEstimate: false },
      } as never,
      OPEN.id,
    )
    expect(candidates.map((c) => c.id)).toEqual(['veh-2', 'veh-3'])
  })
})
