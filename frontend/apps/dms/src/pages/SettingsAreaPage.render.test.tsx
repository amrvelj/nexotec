// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, type FakeRoute } from '../test/fakeBackend'
import type { AreaSettingsRead, SettingChangeRead, SettingDeclarationRead, SettingSlotRead } from '../api/types'
import { SettingsAreaPage } from './SettingsAreaPage'
import { SettingsIndexPage } from './SettingsIndexPage'

// KAN-805 PR-5 — the settings screen pattern (criterion 6) and the three
// things the ticket's Design section asks it to make visible: «not set» as a
// state of its own, a location's inherited value marked as such, the history
// with who, when, before and after (a restore adds an entry).

const ME = (canWrite: boolean): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch' },
    activeDealership: { id: 'd-1', legalName: 'Garage Muster AG' },
    memberships: [{ id: 'd-1', legalName: 'Garage Muster AG' }],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: canWrite ? ALL_GATED_CAPABILITIES : [],
    isActiveDealershipManager: canWrite,
  }),
})

const declaration = (overrides: Partial<SettingDeclarationRead>): SettingDeclarationRead => ({
  key: 'testarea.x',
  area: 'testarea',
  module: 'aftersales',
  kind: 'number',
  whenNotSet: 'quiet',
  actsAs: null,
  minimum: null,
  maximum: null,
  decimals: 0,
  unit: null,
  choices: [],
  checklistItem: false,
  checklistBlocks: null,
  ...overrides,
})

const slot = (overrides: Partial<SettingSlotRead> = {}): SettingSlotRead => ({
  locationId: null,
  isSet: false,
  value: null,
  version: 0,
  changedAt: null,
  changedBy: null,
  fitsDeclaration: true,
  ...overrides,
})

const WAIT = declaration({ key: 'testarea.wait_minutes', unit: 'minutes', checklistItem: true })
const SWITCH = declaration({ key: 'testarea.typed_with_vat', kind: 'switch', whenNotSet: 'refuses' })
const UNIT = declaration({
  key: 'testarea.time_unit',
  kind: 'choice',
  whenNotSet: 'acts_as',
  actsAs: { choice: 'hours' },
  choices: ['hours', 'work_units'],
})
const FEE = declaration({ key: 'testarea.small_parts_fee', kind: 'amount' })
const SURCHARGE = declaration({ key: 'testarea.surcharge', kind: 'amount' })
const NOTE = declaration({ key: 'testarea.desk_note', module: null, kind: 'text' })

const AREA: AreaSettingsRead = {
  area: 'testarea',
  showsLocations: true,
  locations: [
    { id: 'loc-seat', name: 'Hauptsitz', isActive: true },
    { id: 'loc-nord', name: 'Nord', isActive: true },
  ],
  settings: [
    {
      declaration: WAIT,
      dealership: slot({ isSet: true, value: { number: '15' }, version: 1 }),
      locations: [slot({ locationId: 'loc-nord', isSet: true, value: { number: '30' }, version: 1 })],
    },
    { declaration: SWITCH, dealership: slot(), locations: [] },
    {
      declaration: UNIT,
      dealership: slot(),
      locations: [slot({ locationId: 'loc-nord', isSet: true, value: { choice: 'work_units' }, version: 3 })],
    },
    { declaration: FEE, dealership: slot({ version: 2, fitsDeclaration: false }), locations: [] },
    { declaration: SURCHARGE, dealership: slot({ isSet: true, value: { number: '12.50' }, version: 5 }), locations: [] },
    { declaration: NOTE, dealership: slot({ isSet: true, value: { text: { name: 'Bitte klingeln', translations: {} } }, version: 3 }), locations: [] },
  ],
}

const change = (overrides: Partial<SettingChangeRead>): SettingChangeRead => ({
  id: 'c-x',
  key: WAIT.key,
  locationId: null,
  origin: 'edit',
  isSet: true,
  value: { number: '15' },
  versionAfter: 1,
  restoredFromId: null,
  createdAt: '2026-11-25T08:00:00Z',
  createdBy: 'user-2',
  createdByName: 'Beat Keller',
  isSetBefore: false,
  valueBefore: null,
  ...overrides,
})

beforeAll(() => {
  const add = (key: string, value: string) => i18n.addResource('de', 'translation', `settings.${key}`, value)
  add('areas.testarea', 'Werkstatt (Test)')
  add('units.minutes', 'Minuten')
  add('keys.testarea.wait_minutes.label', 'Wartezeit')
  add('keys.testarea.typed_with_vat.label', 'Preise inkl. MwSt. erfasst')
  add('keys.testarea.time_unit.label', 'Zeiteinheit')
  add('keys.testarea.time_unit.options.hours', 'Stunden')
  add('keys.testarea.time_unit.options.work_units', 'Arbeitswerte')
  add('keys.testarea.small_parts_fee.label', 'Kleinteilepauschale')
  add('keys.testarea.desk_note.label', 'Hinweis am Empfang')
  add('keys.testarea.surcharge.label', 'Zuschlag')
})

afterEach(() => cleanup())

function renderArea(routes: FakeRoute[], canWrite = true) {
  const backend = installFakeBackend([
    ME(canWrite),
    { method: 'GET', match: /\/dealerships\/d-1\/setting-areas\/testarea$/, handler: () => AREA },
    ...routes,
  ])
  renderWithProviders(
    <Routes>
      <Route path="/administration/settings/:area" element={<SettingsAreaPage />} />
    </Routes>,
    { route: '/administration/settings/testarea' },
  )
  return backend
}

const rowOf = async (label: string) => {
  const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
  return within(table.getByText(label).closest('tr') as HTMLElement)
}

describe('SettingsAreaPage (KAN-805)', () => {
  it('shows «not set» as a state of its own, with what it does', async () => {
    renderArea([])
    expect(await screen.findByRole('heading', { name: 'Werkstatt (Test)' })).toBeInTheDocument()
    const unit = await rowOf('Zeiteinheit')
    expect(unit.getAllByText('Nicht gesetzt')[0]).toBeInTheDocument()
    expect(unit.getByText('Ohne Wert gilt: Stunden')).toBeInTheDocument()
    const vat = await rowOf('Preise inkl. MwSt. erfasst')
    expect(vat.getByText('Ohne Wert wird abgelehnt, was diese Einstellung braucht.')).toBeInTheDocument()
    // Never «off» or zero for a switch that is not set.
    expect(vat.queryByText('Aus')).toBeNull()
    const fee = await rowOf('Kleinteilepauschale')
    expect(fee.getByText('Gespeicherter Wert passt nicht mehr – gilt als nicht gesetzt')).toBeInTheDocument()
  })

  it('shows a location without a value of its own with the Betrieb’s value, marked as inherited', async () => {
    renderArea([])
    const wait = await rowOf('Wartezeit')
    expect(wait.getByText('15 Minuten')).toBeInTheDocument() // the Betrieb's own
    expect(wait.getByText('Vom Betrieb: 15 Minuten')).toBeInTheDocument() // Hauptsitz inherits
    expect(wait.getByText('30 Minuten')).toBeInTheDocument() // Nord's own
    const vat = await rowOf('Preise inkl. MwSt. erfasst')
    expect(vat.getAllByText('Vom Betrieb: Nicht gesetzt')).toHaveLength(2)
    expect(wait.getByText('Einrichtung')).toBeInTheDocument() // a setup-checklist item
  })

  it('the manager edits a value inline under If-Match on its slot', async () => {
    const backend = renderArea([
      {
        method: 'PUT',
        match: /\/dealerships\/d-1\/setting-values\/testarea\.wait_minutes$/,
        handler: () => slot({ isSet: true, value: { number: '20' }, version: 2 }),
      },
    ])
    const user = userEvent.setup()
    const wait = await rowOf('Wartezeit')
    await user.click(wait.getByText('15 Minuten'))
    const input = await screen.findByRole('textbox', { name: 'Wartezeit · Betrieb' })
    await user.clear(input)
    await user.type(input, '20{Enter}')
    await waitFor(() => expect(backend.callsTo(/setting-values\/testarea\.wait_minutes$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/setting-values\/testarea\.wait_minutes$/, 'PUT')[0]
    expect(call.body).toEqual({ value: { number: '20' } })
    expect(call.headers.get('If-Match')).toBe('1')
  })

  it('a location takes the Betrieb’s value again by clearing its own', async () => {
    const backend = renderArea([
      {
        method: 'PUT',
        match: /\/setting-values\/testarea\.time_unit\/locations\/loc-nord$/,
        handler: () => slot({ locationId: 'loc-nord' }),
      },
    ])
    const user = userEvent.setup()
    const unit = await rowOf('Zeiteinheit')
    expect(unit.getByText('Vom Betrieb: Nicht gesetzt')).toBeInTheDocument() // Hauptsitz inherits
    await user.click(unit.getByText('Arbeitswerte')) // Nord's own, version 3 (the Betrieb's slot is at 0)
    await user.click(await screen.findByRole('option', { name: 'Wert des Betriebs übernehmen' }))
    await waitFor(() => expect(backend.callsTo(/time_unit\/locations\/loc-nord$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/time_unit\/locations\/loc-nord$/, 'PUT')[0]
    expect(call.body).toEqual({ value: null })
    expect(call.headers.get('If-Match')).toBe('3')
  })

  it('a text is edited in a dialog: the name, and a translation only where one is given', async () => {
    const backend = renderArea([
      {
        method: 'PUT',
        match: /\/setting-values\/testarea\.desk_note$/,
        handler: () => slot({ isSet: true, value: { text: { name: 'Bitte läuten', translations: { fr: 'Sonnez svp' } } }, version: 4 }),
      },
    ])
    const user = userEvent.setup()
    const note = await rowOf('Hinweis am Empfang')
    expect(note.getByText('Bitte klingeln')).toBeInTheDocument()
    await user.click(note.getByRole('button', { name: 'Bearbeiten: Hinweis am Empfang · Betrieb' }))
    const dialog = within(await screen.findByRole('dialog'))
    const name = dialog.getByRole('textbox', { name: /^Text/ })
    await user.clear(name)
    await user.type(name, 'Bitte läuten')
    await user.type(dialog.getByRole('textbox', { name: 'Français' }), 'Sonnez svp')
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/desk_note$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/desk_note$/, 'PUT')[0]
    expect(call.body).toEqual({ value: { text: { name: 'Bitte läuten', translations: { fr: 'Sonnez svp' } } } })
    expect(call.headers.get('If-Match')).toBe('3')
  })

  it('the history shows who, when, before and after, and a restore adds an entry', async () => {
    const backend = renderArea([
      {
        method: 'GET',
        match: /\/setting-values\/testarea\.wait_minutes\/history/,
        handler: () => ({
          items: [
            change({ id: 'c-2', value: { number: '15' }, versionAfter: 2, createdBy: 'user-1', createdByName: 'Anna Muster', isSetBefore: true, valueBefore: { number: '10' } }),
            change({ id: 'c-1', value: { number: '10' }, versionAfter: 1 }),
            change({ id: 'c-0', value: { number: '15' }, versionAfter: 0 }),
          ],
          nextCursor: null,
        }),
      },
      {
        method: 'POST',
        match: /\/setting-changes\/c-1\/restore$/,
        handler: () => slot({ isSet: true, value: { number: '10' }, version: 2 }),
      },
    ])
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Verlauf: Wartezeit' }))
    const history = within(await screen.findByRole('list', { name: 'Verlauf' }))
    const entries = history.getAllByRole('listitem')
    expect(within(entries[0]).getByText('Sie')).toBeInTheDocument()
    expect(within(entries[0]).getByText('10 Minuten → 15 Minuten')).toBeInTheDocument()
    expect(within(entries[0]).getByText('Aktuell')).toBeInTheDocument()
    expect(within(entries[1]).getByText('Beat Keller')).toBeInTheDocument()
    expect(within(entries[1]).getByText('Nicht gesetzt → 10 Minuten')).toBeInTheDocument()
    // The current value is not offered for restore, nor an earlier entry
    // holding the same value (it would add nothing); a different one is.
    expect(within(entries[0]).queryByRole('button', { name: /wiederherstellen/ })).toBeNull()
    expect(within(entries[2]).queryByRole('button', { name: /wiederherstellen/ })).toBeNull()
    await user.click(within(entries[1]).getByRole('button', { name: '«10 Minuten» wiederherstellen' }))
    await waitFor(() => expect(backend.callsTo(/setting-changes\/c-1\/restore$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/setting-changes\/c-1\/restore$/, 'POST')[0].headers.get('If-Match')).toBe('1')
  })

  it('a location’s history restores under that location’s own version', async () => {
    const backend = renderArea([
      {
        method: 'GET',
        match: /\/setting-values\/testarea\.time_unit\/history/,
        handler: (req) =>
          req.params.get('locationId') === 'loc-nord'
            ? {
                items: [
                  change({ id: 'n-2', key: UNIT.key, locationId: 'loc-nord', value: { choice: 'work_units' }, versionAfter: 3 }),
                  change({ id: 'n-1', key: UNIT.key, locationId: 'loc-nord', value: { choice: 'hours' }, versionAfter: 2 }),
                ],
                nextCursor: null,
              }
            : { items: [], nextCursor: null },
      },
      { method: 'POST', match: /\/setting-changes\/n-1\/restore$/, handler: () => slot({ locationId: 'loc-nord', version: 4 }) },
    ])
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Verlauf: Zeiteinheit' }))
    expect(await screen.findByText('Noch keine Änderung')).toBeInTheDocument() // the Betrieb's own slot
    await user.click(screen.getByRole('radio', { name: 'Nord' }))
    const history = within(await screen.findByRole('list', { name: 'Verlauf' }))
    await user.click(history.getByRole('button', { name: '«Stunden» wiederherstellen' }))
    await waitFor(() => expect(backend.callsTo(/setting-changes\/n-1\/restore$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/setting-changes\/n-1\/restore$/, 'POST')[0].headers.get('If-Match')).toBe('3')
  })

  it('an amount is typed through the kit’s CHF editor', async () => {
    const backend = renderArea([
      { method: 'PUT', match: /\/setting-values\/testarea\.surcharge$/, handler: () => slot({ isSet: true, value: { number: '15' }, version: 6 }) },
    ])
    const user = userEvent.setup()
    const surcharge = await rowOf('Zuschlag')
    await user.click(surcharge.getByText("CHF 12.50"))
    const input = await screen.findByRole('textbox', { name: 'Zuschlag · Betrieb' })
    expect(input).toHaveValue("CHF 12.5") // the kit's editor drops the fixed decimals while editing
    await user.clear(input)
    await user.type(input, '15{Enter}')
    await waitFor(() => expect(backend.callsTo(/surcharge$/, 'PUT')).toHaveLength(1))
    expect(backend.callsTo(/surcharge$/, 'PUT')[0].body).toEqual({ value: { number: '15' } })
  })

  it('a click and blur on a value that no longer fits writes nothing', async () => {
    const backend = renderArea([])
    const user = userEvent.setup()
    const fee = await rowOf('Kleinteilepauschale')
    await user.click(fee.getByText('Nicht gesetzt'))
    await screen.findByRole('textbox', { name: 'Kleinteilepauschale · Betrieb' })
    await user.click(document.body)
    await user.keyboard('{Tab}')
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(backend.callsTo(/small_parts_fee/, 'PUT')).toHaveLength(0)
  })

  it('every other role reads the values and the history, and changes nothing', async () => {
    renderArea(
      [
        {
          method: 'GET',
          match: /\/setting-values\/testarea\.wait_minutes\/history/,
          handler: () => ({ items: [change({ id: 'c-2', versionAfter: 2 }), change({ id: 'c-1' })], nextCursor: null }),
        },
      ],
      false,
    )
    const user = userEvent.setup()
    expect(await screen.findByText('Nur die Geschäftsführung ändert Einstellungen. Sie sehen die Werte und ihren Verlauf.')).toBeInTheDocument()
    const wait = await rowOf('Wartezeit')
    await user.click(wait.getByText('15 Minuten'))
    expect(screen.queryByRole('textbox', { name: 'Wartezeit · Betrieb' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Verlauf: Wartezeit' }))
    const history = within(await screen.findByRole('list', { name: 'Verlauf' }))
    expect(history.getAllByRole('listitem')).toHaveLength(2)
    expect(history.queryByRole('button', { name: /wiederherstellen/ })).toBeNull()
  })
})

describe('SettingsIndexPage (KAN-805)', () => {
  it('says so when no area brings settings yet', async () => {
    installFakeBackend([ME(true), { method: 'GET', match: /\/dealerships\/d-1\/setting-areas$/, handler: () => ({ items: [] }) }])
    renderWithProviders(<SettingsIndexPage />, { route: '/administration/settings' })
    expect(await screen.findByText('Noch keine Einstellungen')).toBeInTheDocument()
  })

  it('lists the areas with a link to each', async () => {
    installFakeBackend([
      ME(true),
      { method: 'GET', match: /\/dealerships\/d-1\/setting-areas$/, handler: () => ({ items: [{ area: 'testarea', settingCount: 4 }] }) },
    ])
    renderWithProviders(<SettingsIndexPage />, { route: '/administration/settings' })
    expect(await screen.findByRole('link', { name: 'Werkstatt (Test)' })).toHaveAttribute('href', '/administration/settings/testarea')
  })
})
