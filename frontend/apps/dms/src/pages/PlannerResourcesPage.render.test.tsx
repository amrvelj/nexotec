// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import {
  ALL_ENTITLEMENTS,
  ALL_GATED_CAPABILITIES,
  installFakeBackend,
  status,
  type FakeRoute,
  type RecordedCall,
} from '../test/fakeBackend'
import { refusalReasonKey } from '../utils/apiRefusal'
import { PlannerResourcesPage } from './PlannerResourcesPage'

// KAN-808 PR-2 — Dealer Administration › Planner resources (S7).

const BASE = /^\/dealerships\/dealership-1/
const PLANNING = `${BASE.source}/planning`

const entry = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  nameTranslations: {},
  sortOrder: 0,
  version: 1,
  createdAt: '2026-11-25T08:00:00Z',
  ...extra,
})

const WORKPLACE_TYPES = [
  // Named in the Betrieb's correspondence language; the UI language (German) has its translation.
  entry('wt-1', 'Pont élévateur', { nameTranslations: { de: 'Hebebühne', it: 'Ponte sollevatore' }, isSample: true, seedCode: 'lift' }),
  entry('wt-2', 'Waschplatz', { isSample: false, seedCode: null }),
]
const TECHNICIAN_TYPES = [
  entry('tt-1', 'Automobil-Mechatroniker', { isSample: true, seedCode: 'mechatronic', rank: null, countedTimeFactor: null }),
  entry('tt-2', 'Werkstattchef', { isSample: false, seedCode: null, rank: 1, countedTimeFactor: '1.20' }),
]
const QUALIFICATIONS = [entry('q-1', 'Hochvolt', { isMandatory: true })]

const resource = (id: string, extra: Record<string, unknown>) => ({
  id,
  kind: 'person',
  locationId: 'loc-1',
  locationLabel: 'Hauptsitz',
  userId: null,
  userLabel: null,
  personActsHere: null,
  name: null,
  workplaceTypeId: null,
  technicianTypeId: null,
  qualificationIds: [],
  workingHours: [],
  isActive: true,
  sortOrder: 0,
  version: 1,
  createdAt: '2026-11-25T08:00:00Z',
  ...extra,
})

const RESOURCES = [
  resource('r-1', {
    userId: 'user-9',
    userLabel: 'Lea Meier',
    personActsHere: true,
    technicianTypeId: 'tt-2',
    qualificationIds: ['q-1'],
    workingHours: [{ weekday: 1, startsAt: '07:30:00', endsAt: '12:00:00', secondStartsAt: '13:00:00', secondEndsAt: '17:00:00' }],
    version: 3,
  }),
  resource('r-2', { userId: 'user-8', userLabel: 'Marco Rossi', personActsHere: false }),
  resource('r-3', {
    kind: 'workplace',
    locationId: 'loc-2',
    locationLabel: 'Filiale',
    name: 'Bühne 1',
    workplaceTypeId: 'wt-1',
  }),
]

const planningDeclaration = (key: string, extra: Record<string, unknown>) => ({
  key,
  area: 'planning',
  module: 'aftersales',
  kind: 'number',
  whenNotSet: 'refuses',
  actsAs: null,
  minimum: '5',
  maximum: '60',
  decimals: 0,
  unit: 'minutes',
  choices: [],
  checklistItem: true,
  checklistBlocks: null,
  choiceRequires: [],
  divides: null,
  multipleOf: null,
  ...extra,
})
const notSet = { locationId: null, isSet: false, value: null, version: 0, changedAt: null, changedBy: null, fitsDeclaration: true }
const SETTINGS = {
  area: 'planning',
  showsLocations: true,
  locations: [
    { id: 'loc-1', name: 'Hauptsitz', isActive: true },
    { id: 'loc-2', name: 'Filiale', isActive: true },
  ],
  settings: [
    { declaration: planningDeclaration('planning.grid_minutes', { divides: '60' }), dealership: notSet, locations: [] },
    {
      declaration: planningDeclaration('planning.slot_minutes', { maximum: '480', multipleOf: 'planning.grid_minutes' }),
      dealership: notSet,
      locations: [],
    },
  ],
}

function install(options: { canWrite?: boolean; extra?: FakeRoute[] } = {}) {
  const canWrite = options.canWrite ?? true
  return installFakeBackend([
    ...(options.extra ?? []),
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: { id: 'user-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch' },
        activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
        memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
        entitlements: ALL_ENTITLEMENTS,
        // An aftersales role reads the row; only the manager writes it.
        writableCapabilities: canWrite
          ? ALL_GATED_CAPABILITIES
          : ALL_GATED_CAPABILITIES.filter((capability) => capability !== 'planner_resources'),
      }),
    },
    { method: 'GET', match: new RegExp(`${PLANNING}/workplace-types$`), handler: () => ({ items: WORKPLACE_TYPES }) },
    { method: 'GET', match: new RegExp(`${PLANNING}/technician-types$`), handler: () => ({ items: TECHNICIAN_TYPES }) },
    { method: 'GET', match: new RegExp(`${PLANNING}/qualifications$`), handler: () => ({ items: QUALIFICATIONS }) },
    { method: 'GET', match: new RegExp(`${PLANNING}/resources$`), handler: () => ({ items: RESOURCES }) },
    {
      method: 'GET',
      match: new RegExp(`${PLANNING}/person-candidates$`),
      handler: () => ({ items: [{ userId: 'user-7', displayName: 'Nina Keller' }] }),
    },
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/locations$`),
      handler: () => ({
        items: [
          { id: 'loc-1', name: 'Hauptsitz', isActive: true },
          { id: 'loc-2', name: 'Filiale', isActive: true },
        ],
      }),
    },
    { method: 'GET', match: new RegExp(`${BASE.source}/setting-areas/planning$`), handler: () => SETTINGS },
  ])
}

afterEach(() => {
  cleanup()
})

const render = () => renderWithProviders(<PlannerResourcesPage />, { route: '/administration/planner-resources' })
const listTable = (list: string) => screen.findByRole('table', { name: i18n.t(`plannerResources.${list}.title`) })
const resourcesAt = (location: string) =>
  screen.findByRole('table', { name: i18n.t('plannerResources.resources.tableLabel', { location }) })
const calls = (backend: { calls: RecordedCall[] }, method: string, path: RegExp) =>
  backend.calls.filter((call) => call.method === method && path.test(call.pathname))

describe('PlannerResourcesPage (KAN-808)', () => {
  it('lists the types in the UI language, marks a sample, and confirms it with its version', async () => {
    const backend = install({
      extra: [
        {
          method: 'POST',
          match: new RegExp(`${PLANNING}/workplace-types/wt-1/confirmation$`),
          handler: () => ({ ...WORKPLACE_TYPES[0], isSample: false, version: 2 }),
        },
      ],
    })
    render()
    const workplaceTypes = await listTable('workplaceTypes')
    // Ruling 176: the German translation, not the stored French name.
    const lift = within(workplaceTypes).getByRole('row', { name: (n) => n.includes('Hebebühne') })
    expect(within(workplaceTypes).queryByText('Pont élévateur')).toBeNull()
    expect(within(lift).getByText(i18n.t('plannerResources.samples.badge'))).toBeInTheDocument()
    const washing = within(workplaceTypes).getByRole('row', { name: (n) => n.includes('Waschplatz') })
    expect(within(washing).queryByText(i18n.t('plannerResources.samples.badge'))).toBeNull()
    expect(within(washing).queryByRole('button', { name: (n) => n.startsWith(i18n.t('plannerResources.samples.confirm')) })).toBeNull()
    // A type a resource uses cannot be deleted.
    expect(within(lift).getByRole('button', { name: i18n.t('plannerResources.actions.deleteNamed', { name: 'Hebebühne' }) })).toBeDisabled()
    expect(within(washing).getByRole('button', { name: i18n.t('plannerResources.actions.deleteNamed', { name: 'Waschplatz' }) })).toBeEnabled()

    // Technician types: rank and counted-time factor; a sample has neither.
    const technicianTypes = await listTable('technicianTypes')
    const chief = within(technicianTypes).getByRole('row', { name: (n) => n.includes('Werkstattchef') })
    expect(within(chief).getByText('1.20')).toBeInTheDocument()
    const mechatronic = within(technicianTypes).getByRole('row', { name: (n) => n.includes('Automobil-Mechatroniker') })
    expect(within(mechatronic).getByText(i18n.t('plannerResources.technicianTypes.noFactor'))).toBeInTheDocument()
    expect(within(mechatronic).getByText(i18n.t('plannerResources.samples.badge'))).toBeInTheDocument()

    // Qualifications: «mandatory» shown.
    const qualifications = await listTable('qualifications')
    const highVoltage = within(qualifications).getByRole('row', { name: (n) => n.includes('Hochvolt') })
    expect(within(highVoltage).getByText(i18n.t('plannerResources.qualifications.mandatory'))).toBeInTheDocument()

    await userEvent.click(
      within(lift).getByRole('button', { name: i18n.t('plannerResources.samples.confirmNamed', { name: 'Hebebühne' }) }),
    )
    await waitFor(() => expect(calls(backend, 'POST', /\/workplace-types\/wt-1\/confirmation$/)).toHaveLength(1))
    expect(calls(backend, 'POST', /\/confirmation$/)[0].headers.get('If-Match')).toBe('1')
  })

  it('changes a technician type’s factor and rank, sending only what changed with If-Match', async () => {
    const backend = install({
      extra: [{ method: 'PATCH', match: new RegExp(`${PLANNING}/technician-types/tt-2$`), handler: () => TECHNICIAN_TYPES[1] }],
    })
    render()
    const technicianTypes = await listTable('technicianTypes')
    await userEvent.click(
      within(technicianTypes).getByRole('button', { name: i18n.t('plannerResources.actions.editNamed', { name: 'Werkstattchef' }) }),
    )
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.technicianTypes.editTitle') })
    const factor = within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.entryDialog.factor')) })
    await userEvent.clear(factor)
    await userEvent.type(factor, '1.25')
    const rank = within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.entryDialog.rank')) })
    await userEvent.clear(rank)
    await userEvent.type(rank, '2')
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(calls(backend, 'PATCH', /\/technician-types\/tt-2$/)).toHaveLength(1))
    const patch = calls(backend, 'PATCH', /\/technician-types\/tt-2$/)[0]
    expect(patch.headers.get('If-Match')).toBe('1')
    // A decimal string (ADR-217); the name and translations are not sent, unchanged.
    expect(patch.body).toEqual({ rank: 2, countedTimeFactor: '1.25' })
  })

  it('adds a qualification with its translations and «mandatory»', async () => {
    const backend = install({
      extra: [{ method: 'POST', match: new RegExp(`${PLANNING}/qualifications$`), handler: () => QUALIFICATIONS[0] }],
    })
    render()
    await listTable('qualifications')
    await userEvent.click(screen.getByRole('button', { name: i18n.t('plannerResources.qualifications.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.qualifications.createTitle') })
    const [name] = within(dialog).getAllByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.entryDialog.name')) })
    await userEvent.type(name, 'Klimaanlage')
    await userEvent.type(
      within(dialog).getByRole('textbox', {
        name: i18n.t('plannerResources.entryDialog.translation', { language: i18n.t('plannerResources.languages.fr') }),
      }),
      'Climatisation',
    )
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: (n) => n.startsWith(i18n.t('plannerResources.entryDialog.mandatory')) }),
    )
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(calls(backend, 'POST', /\/qualifications$/)).toHaveLength(1))
    expect(calls(backend, 'POST', /\/qualifications$/)[0].body).toEqual({
      name: 'Klimaanlage',
      nameTranslations: { fr: 'Climatisation' },
      isMandatory: true,
    })
  })

  it('adds a person picked from the candidates with the kit’s Picker', async () => {
    const backend = install({
      extra: [{ method: 'POST', match: new RegExp(`${PLANNING}/resources$`), handler: () => RESOURCES[0] }],
    })
    render()
    await resourcesAt('Hauptsitz')
    await userEvent.click(screen.getByRole('button', { name: i18n.t('plannerResources.resources.addPerson') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.resourceDialog.personCreateTitle') })
    expect(within(dialog).getByRole('button', { name: i18n.t('common.save') })).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('plannerResources.resourceDialog.choosePerson') }))
    await userEvent.type(
      await screen.findByRole('textbox', { name: i18n.t('plannerResources.resourceDialog.searchPerson') }),
      'Nin',
    )
    await userEvent.click(await screen.findByText('Nina Keller'))
    const form = await screen.findByRole('dialog', { name: i18n.t('plannerResources.resourceDialog.personCreateTitle') })
    expect(within(form).getByText('Nina Keller')).toBeInTheDocument()
    // Two locations: none is chosen for the manager.
    await userEvent.click(within(form).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.columns.location')) }))
    await userEvent.click(await screen.findByRole('option', { name: 'Filiale' }))
    await userEvent.click(within(form).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(calls(backend, 'POST', /\/planning\/resources$/)).toHaveLength(1))
    expect(calls(backend, 'POST', /\/planning\/resources$/)[0].body).toEqual({
      kind: 'person',
      userId: 'user-7',
      locationId: 'loc-2',
      technicianTypeId: null,
      qualificationIds: [],
      sortOrder: 0,
    })
  })

  it('replaces a person’s working week as a PUT with If-Match, a second interval included', async () => {
    const backend = install({
      extra: [{ method: 'PUT', match: new RegExp(`${PLANNING}/resources/r-1/working-hours$`), handler: () => RESOURCES[0] }],
    })
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    const lea = within(hauptsitz).getByRole('row', { name: (n) => n.includes('Lea Meier') })
    await userEvent.click(within(lea).getByRole('button', { name: i18n.t('plannerResources.hours.openNamed', { name: 'Lea Meier' }) }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.hours.title', { name: 'Lea Meier' }) })
    const tuesday = i18n.t('locations.hours.weekdays.2')
    await userEvent.click(within(dialog).getByRole('checkbox', { name: tuesday }))
    const end = within(dialog).getByLabelText(`${tuesday} ${i18n.t('plannerResources.hours.endsAt')}`)
    await userEvent.clear(end)
    await userEvent.type(end, '12:00')
    await userEvent.type(within(dialog).getByLabelText(`${tuesday} ${i18n.t('plannerResources.hours.secondStartsAt')}`), '13:00')
    await userEvent.type(within(dialog).getByLabelText(`${tuesday} ${i18n.t('plannerResources.hours.secondEndsAt')}`), '16:30')
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(calls(backend, 'PUT', /\/working-hours$/)).toHaveLength(1))
    const put = calls(backend, 'PUT', /\/working-hours$/)[0]
    expect(put.headers.get('If-Match')).toBe('3')
    expect(put.body).toEqual({
      days: [
        { weekday: 1, startsAt: '07:30', endsAt: '12:00', secondStartsAt: '13:00', secondEndsAt: '17:00' },
        { weekday: 2, startsAt: '07:30', endsAt: '12:00', secondStartsAt: '13:00', secondEndsAt: '16:30' },
      ],
    })
  })

  it('names a day whose times cannot be true and sends nothing', async () => {
    const backend = install()
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    await userEvent.click(
      within(hauptsitz).getByRole('button', { name: i18n.t('plannerResources.hours.openNamed', { name: 'Lea Meier' }) }),
    )
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.hours.title', { name: 'Lea Meier' }) })
    const monday = i18n.t('locations.hours.weekdays.1')
    await userEvent.clear(within(dialog).getByLabelText(`${monday} ${i18n.t('plannerResources.hours.secondEndsAt')}`))
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    expect(await within(dialog).findByText(i18n.t('plannerResources.hours.invalid', { day: monday }))).toBeInTheDocument()
    expect(calls(backend, 'PUT', /\/working-hours$/)).toEqual([])
  })

  it('marks a person who no longer works in the Betrieb, groups by location and filters one', async () => {
    install()
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    const marco = within(hauptsitz).getByRole('row', { name: (n) => n.includes('Marco Rossi') })
    expect(within(marco).getByText(i18n.t('plannerResources.status.noLongerWorksHere'))).toBeInTheDocument()
    const lea = within(hauptsitz).getByRole('row', { name: (n) => n.includes('Lea Meier') })
    expect(within(lea).queryByText(i18n.t('plannerResources.status.noLongerWorksHere'))).toBeNull()
    expect(within(lea).getByText('Hochvolt')).toBeInTheDocument()
    expect(within(lea).getByText('Werkstattchef')).toBeInTheDocument()
    // The workplace sits at the other location, typed in the UI language.
    const filiale = await resourcesAt('Filiale')
    expect(within(filiale).getByText('Bühne 1')).toBeInTheDocument()
    expect(within(filiale).getByText('Hebebühne')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('textbox', { name: i18n.t('plannerResources.resources.filter') }))
    await userEvent.click(await screen.findByRole('option', { name: 'Filiale' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('table', { name: i18n.t('plannerResources.resources.tableLabel', { location: 'Hauptsitz' }) }),
      ).toBeNull(),
    )
    expect(await resourcesAt('Filiale')).toBeInTheDocument()
  })

  it('deactivates a resource after saying what happens to its bookings, with If-Match', async () => {
    const backend = install({
      extra: [{ method: 'PATCH', match: new RegExp(`${PLANNING}/resources/r-1$`), handler: () => RESOURCES[0] }],
    })
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    await userEvent.click(
      within(hauptsitz).getByRole('button', { name: i18n.t('plannerResources.actions.deactivateNamed', { name: 'Lea Meier' }) }),
    )
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.deactivate.title') })
    expect(within(dialog).getByText(i18n.t('plannerResources.deactivate.body', { name: 'Lea Meier' }))).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('plannerResources.actions.deactivate') }))
    await waitFor(() => expect(calls(backend, 'PATCH', /\/resources\/r-1$/)).toHaveLength(1))
    const patch = calls(backend, 'PATCH', /\/resources\/r-1$/)[0]
    expect(patch.headers.get('If-Match')).toBe('3')
    expect(patch.body).toEqual({ isActive: false })
  })

  it('shows a refusal in the UI language and offers a reload only for a stale version', async () => {
    const backend = install({
      extra: [
        {
          method: 'POST',
          match: new RegExp(`${PLANNING}/workplace-types/wt-1/confirmation$`),
          handler: () =>
            status(409, { error: { code: 'conflict', message: 'stale', details: { currentVersion: 2, ifMatchVersion: 1 } } }),
        },
        {
          method: 'DELETE',
          match: new RegExp(`${PLANNING}/workplace-types/wt-2$`),
          handler: () =>
            status(409, { error: { code: 'conflict', message: 'in use', details: { reason: 'planning_list_entry_in_use' } } }),
        },
      ],
    })
    render()
    const workplaceTypes = await listTable('workplaceTypes')
    // A business refusal: its sentence, no reload.
    await userEvent.click(
      within(workplaceTypes).getByRole('button', { name: i18n.t('plannerResources.actions.deleteNamed', { name: 'Waschplatz' }) }),
    )
    const confirm = await screen.findByRole('dialog', { name: i18n.t('plannerResources.workplaceTypes.deleteTitle') })
    await userEvent.click(within(confirm).getByRole('button', { name: i18n.t('plannerResources.actions.delete') }))
    expect(
      await within(confirm).findByText(i18n.t(refusalReasonKey('planning_list_entry_in_use')!)),
    ).toBeInTheDocument()
    expect(within(confirm).queryByRole('button', { name: i18n.t('common.inlineEdit.reload') })).toBeNull()
    await userEvent.click(within(confirm).getByRole('button', { name: i18n.t('common.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    // A stale version: the refusal and «Neu laden», which reads the lists again.
    await userEvent.click(
      within(workplaceTypes).getByRole('button', { name: i18n.t('plannerResources.samples.confirmNamed', { name: 'Hebebühne' }) }),
    )
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(i18n.t('apiRefusal.staleVersion'))).toBeInTheDocument()
    const before = calls(backend, 'GET', /\/workplace-types$/).length
    await userEvent.click(within(alert).getByRole('button', { name: i18n.t('common.inlineEdit.reload') }))
    await waitFor(() => expect(calls(backend, 'GET', /\/workplace-types$/).length).toBeGreaterThan(before))
  })

  it('without the write: no add, edit, confirm or delete; the working week opens read-only', async () => {
    const backend = install({ canWrite: false })
    render()
    const workplaceTypes = await listTable('workplaceTypes')
    expect(screen.getByText(i18n.t('plannerResources.readOnly'))).toBeInTheDocument()
    // The sample is still shown as one — only its confirmation is the manager's.
    expect(within(workplaceTypes).getByText(i18n.t('plannerResources.samples.badge'))).toBeInTheDocument()
    expect(within(workplaceTypes).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('button', { name: i18n.t('plannerResources.workplaceTypes.add') })).toBeNull()
    expect(screen.queryByRole('button', { name: i18n.t('plannerResources.resources.addPerson') })).toBeNull()
    const hauptsitz = await resourcesAt('Hauptsitz')
    expect(
      within(hauptsitz).queryByRole('button', { name: i18n.t('plannerResources.actions.editNamed', { name: 'Lea Meier' }) }),
    ).toBeNull()
    expect(
      within(hauptsitz).queryByRole('button', { name: i18n.t('plannerResources.actions.deactivateNamed', { name: 'Lea Meier' }) }),
    ).toBeNull()
    await userEvent.click(
      within(hauptsitz).getByRole('button', { name: i18n.t('plannerResources.hours.openNamed', { name: 'Lea Meier' }) }),
    )
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.hours.title', { name: 'Lea Meier' }) })
    expect(within(dialog).getByText('07:30')).toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox')).toBeNull()
    expect(within(dialog).queryByRole('button', { name: i18n.t('common.save') })).toBeNull()
    // The candidates are read under the write row: a reader never asks.
    expect(calls(backend, 'GET', /\/person-candidates$/)).toEqual([])
  })

  it('embeds the settings rows of O6 and O7 (KAN-805 criterion 6), with their rules hinted', async () => {
    install()
    render()
    const table = await screen.findByRole('table', { name: i18n.t('settings.tableLabel') })
    for (const key of ['grid_minutes', 'slot_minutes']) {
      expect(within(table).getByText(i18n.t(`settings.keys.planning.${key}.label`))).toBeInTheDocument()
    }
    // «Not set» as a state of its own in each of the three columns of both rows.
    expect(within(table).getAllByText(i18n.t('settings.notSet.label')).length).toBeGreaterThanOrEqual(2)
    expect(table).toHaveTextContent(
      i18n.t('settings.rule.divides', { number: '60', values: '5, 6, 10, 12, 15, 20, 30, 60' }),
    )
    expect(table).toHaveTextContent(
      i18n.t('settings.rule.multipleOfNotSet', { setting: i18n.t('settings.keys.planning.grid_minutes.label') }),
    )
  })

  // KAN-842 — the review follow-ups of KAN-808 PR-2.
  it('creates a workplace with its name, location and type', async () => {
    const backend = install({
      extra: [{ method: 'POST', match: new RegExp(`${PLANNING}/resources$`), handler: () => status(201, RESOURCES[2]) }],
    })
    render()
    await resourcesAt('Hauptsitz')
    await userEvent.click(screen.getByRole('button', { name: i18n.t('plannerResources.resources.addWorkplace') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.resourceDialog.workplaceCreateTitle') })
    await userEvent.type(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.resourceDialog.workplaceName')) }), 'Bühne 2')
    await userEvent.click(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.columns.location')) }))
    await userEvent.click(await screen.findByRole('option', { name: 'Filiale' }))
    await userEvent.click(within(dialog).getByRole('textbox', { name: i18n.t('plannerResources.columns.workplaceType') }))
    await userEvent.click(await screen.findByRole('option', { name: 'Waschplatz' }))
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(calls(backend, 'POST', /\/planning\/resources$/)).toHaveLength(1))
    expect(calls(backend, 'POST', /\/planning\/resources$/)[0].body).toEqual({
      kind: 'workplace', locationId: 'loc-2', sortOrder: 0, name: 'Bühne 2', workplaceTypeId: 'wt-2',
    })
  })

  it('edits a workplace sending only what changed, with If-Match', async () => {
    const backend = install({
      extra: [{ method: 'PATCH', match: new RegExp(`${PLANNING}/resources/r-3$`), handler: () => ({ ...RESOURCES[2], name: 'Bühne 9', version: 2 }) }],
    })
    render()
    const filiale = await resourcesAt('Filiale')
    await userEvent.click(within(filiale).getByRole('button', { name: i18n.t('plannerResources.actions.editNamed', { name: 'Bühne 1' }) }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.resourceDialog.workplaceEditTitle') })
    const nameField = within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.resourceDialog.workplaceName')) })
    await userEvent.clear(nameField)
    await userEvent.type(nameField, 'Bühne 9')
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(calls(backend, 'PATCH', /\/resources\/r-3$/)).toHaveLength(1))
    const patch = calls(backend, 'PATCH', /\/resources\/r-3$/)[0]
    expect(patch.body).toEqual({ name: 'Bühne 9' })
    expect(patch.headers.get('If-Match')).toBe('1')
  })

  it('edits a person sending only what changed, and says what moving to another location does', async () => {
    const backend = install({
      extra: [{ method: 'PATCH', match: new RegExp(`${PLANNING}/resources/r-1$`), handler: () => ({ ...RESOURCES[0], locationId: 'loc-2', version: 4 }) }],
    })
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    await userEvent.click(within(hauptsitz).getByRole('button', { name: i18n.t('plannerResources.actions.editNamed', { name: 'Lea Meier' }) }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('plannerResources.resourceDialog.personEditTitle') })
    expect(within(dialog).queryByText(i18n.t('plannerResources.resourceDialog.movingNote'))).toBeNull()
    await userEvent.click(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('plannerResources.columns.location')) }))
    await userEvent.click(await screen.findByRole('option', { name: 'Filiale' }))
    expect(within(dialog).getByText(i18n.t('plannerResources.resourceDialog.movingNote'))).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(calls(backend, 'PATCH', /\/resources\/r-1$/)).toHaveLength(1))
    const patch = calls(backend, 'PATCH', /\/resources\/r-1$/)[0]
    expect(patch.body).toEqual({ locationId: 'loc-2' })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('reactivates a resource with If-Match, once however often it is clicked', async () => {
    const inactive = resource('r-4', { kind: 'workplace', name: 'Bühne alt', isActive: false, version: 5 })
    let release: () => void = () => {}
    const backend = install({
      extra: [
        { method: 'GET', match: new RegExp(`${PLANNING}/resources$`), handler: () => ({ items: [...RESOURCES, inactive] }) },
        {
          method: 'PATCH',
          match: new RegExp(`${PLANNING}/resources/r-4$`),
          // Held until the test lets it go: the second click lands while the first is in flight.
          handler: () => new Promise((resolve) => { release = () => resolve({ ...inactive, isActive: true, version: 6 }) }),
        },
      ],
    })
    render()
    const hauptsitz = await resourcesAt('Hauptsitz')
    const reactivate = within(hauptsitz).getByRole('button', { name: i18n.t('plannerResources.actions.reactivateNamed', { name: 'Bühne alt' }) })
    await userEvent.dblClick(reactivate)
    await waitFor(() => expect(calls(backend, 'PATCH', /\/resources\/r-4$/)).toHaveLength(1))
    release()
    await waitFor(() => expect(calls(backend, 'GET', /\/planning\/resources$/).length).toBeGreaterThan(1))
    const patches = calls(backend, 'PATCH', /\/resources\/r-4$/)
    expect(patches).toHaveLength(1)
    expect(patches[0].body).toEqual({ isActive: true })
    expect(patches[0].headers.get('If-Match')).toBe('5')
  })

  it('confirms a sample once on a double click', async () => {
    let release: () => void = () => {}
    const backend = install({
      extra: [
        {
          method: 'POST',
          match: new RegExp(`${PLANNING}/workplace-types/wt-1/confirmation$`),
          handler: () => new Promise((resolve) => { release = () => resolve({ ...WORKPLACE_TYPES[0], isSample: false, version: 2 }) }),
        },
      ],
    })
    render()
    const workplaceTypes = await listTable('workplaceTypes')
    await userEvent.dblClick(within(workplaceTypes).getByRole('button', { name: i18n.t('plannerResources.samples.confirmNamed', { name: 'Hebebühne' }) }))
    await waitFor(() => expect(calls(backend, 'POST', /\/confirmation$/)).toHaveLength(1))
    release()
    await waitFor(() => expect(calls(backend, 'GET', /\/workplace-types$/).length).toBeGreaterThan(1))
    expect(calls(backend, 'POST', /\/confirmation$/)).toHaveLength(1)
    expect(screen.queryByText(i18n.t('apiRefusal.staleVersion'))).toBeNull()
  })

  it('a role that may not read sees the load error and no «you see the entries» note', async () => {
    const forbidden = () => status(403, { error: { code: 'forbidden', message: 'no', details: null } })
    install({
      canWrite: false,
      extra: [
        { method: 'GET', match: new RegExp(`${PLANNING}/workplace-types$`), handler: forbidden },
        { method: 'GET', match: new RegExp(`${PLANNING}/technician-types$`), handler: forbidden },
        { method: 'GET', match: new RegExp(`${PLANNING}/qualifications$`), handler: forbidden },
        { method: 'GET', match: new RegExp(`${PLANNING}/resources$`), handler: forbidden },
      ],
    })
    render()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('plannerResources.readOnly'))).toBeNull()
  })

  it('groups the resources only once the locations are known: no «Standort inaktiv» meanwhile', async () => {
    let releaseLocations: () => void = () => {}
    install({
      extra: [
        {
          method: 'GET',
          match: new RegExp(`${BASE.source}/locations$`),
          handler: () =>
            new Promise((resolve) => {
              releaseLocations = () =>
                resolve({ items: [{ id: 'loc-1', name: 'Hauptsitz', isActive: true }, { id: 'loc-2', name: 'Filiale', isActive: true }] })
            }),
        },
      ],
    })
    render()
    await listTable('workplaceTypes')
    expect(screen.queryByText(i18n.t('plannerResources.resources.locationInactive'))).toBeNull()
    releaseLocations()
    await resourcesAt('Hauptsitz')
    expect(screen.queryByText(i18n.t('plannerResources.resources.locationInactive'))).toBeNull()
  })

  it('a failed read of the locations is said, not a silently missing resource list (KAN-842)', async () => {
    install({
      extra: [{ method: 'GET', match: new RegExp(`${BASE.source}/locations$`), handler: () => status(503, { error: { code: 'unavailable', message: 'down', details: null } }) }],
    })
    render()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
