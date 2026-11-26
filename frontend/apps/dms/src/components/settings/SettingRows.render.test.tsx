// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, type FakeBackend, type FakeRoute } from '../../test/fakeBackend'
import type { AreaSettingsRead, SettingDeclarationRead, SettingSlotRead } from '../../api/types'
import { SettingRows } from './SettingRows'

// KAN-805 criterion 6 (2026-11-25): a topic screen embeds the same rows for
// the settings it holds — only those, in its order — and a deactivated
// location shows only while it still holds a value of its own, read-only.

const declaration = (key: string): SettingDeclarationRead => ({
  key,
  area: 'testarea',
  module: null,
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
  choiceRequires: [],
  divides: null,
  multipleOf: null,
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

const area = (oldSlot: SettingSlotRead | null): AreaSettingsRead => ({
  area: 'testarea',
  showsLocations: true,
  locations: [
    { id: 'loc-a', name: 'Hauptsitz', isActive: true },
    { id: 'loc-b', name: 'Filiale', isActive: true },
    { id: 'loc-old', name: 'Alte Filiale', isActive: false },
  ],
  settings: [
    { declaration: declaration('testarea.one'), dealership: slot({ isSet: true, value: { number: '1' }, version: 1 }), locations: oldSlot ? [oldSlot] : [] },
    { declaration: declaration('testarea.two'), dealership: slot({ isSet: true, value: { number: '2' }, version: 1 }), locations: [] },
    { declaration: declaration('testarea.three'), dealership: slot({ isSet: true, value: { number: '3' }, version: 1 }), locations: [] },
  ],
})

beforeAll(() => {
  for (const [name, label] of [['one', 'Eins'], ['two', 'Zwei'], ['three', 'Drei']]) {
    i18n.addResource('de', 'translation', `settings.keys.testarea.${name}.label`, label)
  }
})
afterEach(() => cleanup())

function render(data: AreaSettingsRead, keys?: string[], extra: FakeRoute[] = []): FakeBackend {
  const backend = installFakeBackend([
    ...extra,
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: { id: 'user-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch' },
        activeDealership: { id: 'd-1', legalName: 'Garage Muster AG' },
        memberships: [{ id: 'd-1', legalName: 'Garage Muster AG' }],
        entitlements: ALL_ENTITLEMENTS,
        writableCapabilities: ALL_GATED_CAPABILITIES,
        isActiveDealershipManager: true,
      }),
    },
    {
      method: 'GET',
      match: /\/setting-values\/testarea\.one\/history/,
      handler: () => ({
        items: [
          { id: 'c-2', key: 'testarea.one', locationId: 'loc-old', origin: 'edit', isSet: true, value: { number: '9' }, versionAfter: 2, restoredFromId: null, createdAt: '2026-11-25T08:00:00Z', createdBy: null, createdByName: null, isSetBefore: true, valueBefore: { number: '8' } },
          { id: 'c-1', key: 'testarea.one', locationId: 'loc-old', origin: 'edit', isSet: true, value: { number: '8' }, versionAfter: 1, restoredFromId: null, createdAt: '2026-11-24T08:00:00Z', createdBy: null, createdByName: null, isSetBefore: false, valueBefore: null },
        ],
        nextCursor: null,
      }),
    },
  ])
  renderWithProviders(<SettingRows dealershipId="d-1" data={data} keys={keys} onReload={() => {}} />)
  return backend
}

const rowLabels = async () => {
  const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
  return table
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('td')?.textContent)
}

describe('SettingRows (KAN-805)', () => {
  it('a topic screen renders only the settings it holds, in its own order', async () => {
    render(area(null), ['testarea.three', 'testarea.one'])
    expect(await rowLabels()).toEqual(['Drei', 'Eins'])
  })

  it('the area screen renders every setting of the area', async () => {
    render(area(null))
    expect(await rowLabels()).toEqual(['Eins', 'Zwei', 'Drei'])
  })

  it('a deactivated location whose value was cleared gets no column', async () => {
    render(area(slot({ locationId: 'loc-old', version: 2 })))
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    expect(table.queryByRole('columnheader', { name: /Alte Filiale/ })).toBeNull()
  })

  it('a deactivated location holding only a value that no longer fits keeps its column', async () => {
    render(area(slot({ locationId: 'loc-old', version: 2, fitsDeclaration: false })))
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    expect(table.getByRole('columnheader', { name: /^Alte Filiale/ })).toBeInTheDocument()
  })

  it('a deactivated location still holding a value is shown read-only, its history without restore', async () => {
    render(area(slot({ locationId: 'loc-old', isSet: true, value: { number: '9' }, version: 2 })))
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    expect(table.getByRole('columnheader', { name: /^Alte Filiale\s*\(inaktiv\)$/ })).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(table.getByText('9'))
    expect(screen.queryByRole('textbox', { name: 'Eins · Alte Filiale' })).toBeNull()
    await user.click(table.getByRole('button', { name: 'Verlauf: Eins' }))
    await user.click(await screen.findByRole('radio', { name: 'Alte Filiale' }))
    const history = within(await screen.findByRole('list', { name: 'Verlauf' }))
    expect(history.getAllByRole('listitem')).toHaveLength(2)
    expect(history.queryByRole('button', { name: /wiederherstellen/ })).toBeNull()
  })

  // KAN-806 (check 49): one render and one opened editor per column. The write right comes from
  // /auth/me, which answers after the table renders: a click before it opens no editor, so wait
  // until the cell is editable (InlineEditField's pointer cursor).
  const unitArea = (): AreaSettingsRead => {
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.label', 'Einheit')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.options.hours', 'Stunden')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.options.work_units', 'Arbeitswerte')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.per_hour.label', 'Pro Stunde')
    return {
      area: 'testarea',
      showsLocations: true,
      locations: [{ id: 'loc-a', name: 'Hauptsitz', isActive: true }],
      settings: [
        {
          declaration: {
            ...declaration('testarea.unit'),
            kind: 'choice',
            choices: ['hours', 'work_units'],
            choiceRequires: [{ choice: 'work_units', key: 'testarea.per_hour' }],
          },
          dealership: slot(),
          locations: [],
        },
        {
          declaration: declaration('testarea.per_hour'),
          dealership: slot(),
          // Only the location holds a value per hour.
          locations: [slot({ locationId: 'loc-a', isSet: true, value: { number: '12' }, version: 1 })],
        },
      ],
    }
  }
  const workUnitsOptionIn = async (column: number) => {
    render(unitArea(), ['testarea.unit', 'testarea.per_hour'])
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    const unitRow = table.getAllByRole('row').find((row) => row.querySelector('td')?.textContent === 'Einheit')!
    const cell = Array.from(unitRow.querySelectorAll('td'))[column]
    await waitFor(() => expect(cell.querySelector('span[style]')).toHaveStyle({ cursor: 'pointer' }))
    await userEvent.click(cell.querySelector('span[style]') as HTMLElement)
    return screen.findByRole('option', { name: 'Arbeitswerte' })
  }

  it('offers a choice that needs another setting disabled where that one is not in force (KAN-806, check 49)', async () => {
    // The Betrieb's column: no value per hour in force there.
    expect(await workUnitsOptionIn(1)).toHaveAttribute('data-combobox-disabled', 'true')
  })

  it('offers that choice where the other setting is in force at the location (KAN-806, check 49)', async () => {
    // The location holds its own value per hour.
    expect(await workUnitsOptionIn(2)).not.toHaveAttribute('data-combobox-disabled')
  })

  it('counts a required setting whose «not set» acts as a value as in force, as the API does (check 49)', async () => {
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.label', 'Einheit')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.options.hours', 'Stunden')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.unit.options.work_units', 'Arbeitswerte')
    i18n.addResource('de', 'translation', 'settings.keys.testarea.per_hour.label', 'Pro Stunde')
    const data: AreaSettingsRead = {
      area: 'testarea',
      showsLocations: false,
      locations: [],
      settings: [
        {
          declaration: {
            ...declaration('testarea.unit'),
            kind: 'choice',
            choices: ['hours', 'work_units'],
            choiceRequires: [{ choice: 'work_units', key: 'testarea.per_hour' }],
          },
          dealership: slot(),
          locations: [],
        },
        {
          // Never written, but «not set» acts as 10 — the store counts that as in force.
          declaration: { ...declaration('testarea.per_hour'), whenNotSet: 'acts_as', actsAs: { number: '10' } },
          dealership: slot(),
          locations: [],
        },
      ],
    }
    render(data, ['testarea.unit', 'testarea.per_hour'])
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    const unitRow = table.getAllByRole('row').find((row) => row.querySelector('td')?.textContent === 'Einheit')!
    const betrieb = Array.from(unitRow.querySelectorAll('td'))[1]
    await waitFor(() => expect(betrieb.querySelector('span[style]')).toHaveStyle({ cursor: 'pointer' }))
    await userEvent.click(betrieb.querySelector('span[style]') as HTMLElement)
    expect(await screen.findByRole('option', { name: 'Arbeitswerte' })).not.toHaveAttribute('data-combobox-disabled')
  })

  // KAN-808 (PR-1c review finding 5): O6 divides 60, O7 is a multiple of O6 — named by the
  // area API, hinted beside the value (the info icon) and offered or checked before the write.
  const plannerArea = ({ grid = '15', slot: slotValue = null as string | null, locationGrid = null as string | null } = {}): AreaSettingsRead => ({
    area: 'planning',
    showsLocations: true,
    locations: [
      { id: 'loc-a', name: 'Hauptsitz', isActive: true },
      { id: 'loc-b', name: 'Filiale', isActive: true },
    ],
    settings: [
      {
        declaration: {
          ...declaration('planning.grid_minutes'),
          area: 'planning',
          module: 'aftersales',
          whenNotSet: 'refuses',
          minimum: '5',
          maximum: '60',
          unit: 'minutes',
          divides: '60',
        },
        dealership: slot({ isSet: true, value: { number: grid }, version: 1 }),
        locations: locationGrid ? [slot({ locationId: 'loc-a', isSet: true, value: { number: locationGrid }, version: 1 })] : [],
      },
      {
        declaration: {
          ...declaration('planning.slot_minutes'),
          area: 'planning',
          module: 'aftersales',
          whenNotSet: 'refuses',
          minimum: '5',
          maximum: '480',
          unit: 'minutes',
          multipleOf: 'planning.grid_minutes',
        },
        dealership: slotValue ? slot({ isSet: true, value: { number: slotValue }, version: 1 }) : slot(),
        locations: [],
      },
    ],
  })
  const rowCell = async (label: string, column: number) => {
    const table = within(await screen.findByRole('table', { name: 'Einstellungen' }))
    const row = table.getAllByRole('row').find((r) => r.querySelector('td')?.textContent === label)!
    return Array.from(row.querySelectorAll('td'))[column]
  }
  // The write right comes from /auth/me, after the first render: wait for the editable state.
  const openEditor = async (cell: HTMLElement) => {
    const editable = () => Array.from(cell.querySelectorAll('span')).find((span) => span.style.cursor === 'pointer')
    await waitFor(() => expect(editable()).toBeTruthy())
    await userEvent.click(editable()!)
  }
  const gridLabel = () => i18n.t('settings.keys.planning.grid_minutes.label')
  const slotLabel = () => i18n.t('settings.keys.planning.slot_minutes.label')

  it('hints O6 as a divisor of 60 beside its value and offers only those divisors (KAN-808)', async () => {
    render(plannerArea({ slot: '45' }), ['planning.grid_minutes', 'planning.slot_minutes'])
    const betrieb = await rowCell(gridLabel(), 1)
    expect(within(betrieb).getByRole('img', { name: i18n.t('common.fieldHintIcon') })).toBeInTheDocument()
    expect(betrieb).toHaveTextContent(
      i18n.t('settings.rule.divides', { number: '60', values: '5, 6, 10, 12, 15, 20, 30, 60' }),
    )
    expect(betrieb).toHaveTextContent(i18n.t('settings.rule.dividesSetting', { setting: slotLabel(), values: '45' }))
    await openEditor(betrieb)
    const options = await screen.findAllByRole('option')
    const minutes = (n: number) => i18n.t('settings.value.withUnit', { number: String(n), unit: i18n.t('settings.units.minutes') })
    expect(options.map((o) => o.textContent)).toEqual([
      ...[5, 6, 10, 12, 15, 20, 30, 60].map(minutes),
      i18n.t('settings.clear.dealership'),
    ])
    // O7 is 45 here: only a grid it stays a multiple of is offered enabled.
    const enabled = options.filter((o) => !o.hasAttribute('data-combobox-disabled')).map((o) => o.textContent)
    expect(enabled).toEqual([minutes(5), minutes(15), i18n.t('settings.clear.dealership')])
  })

  it('refuses an O7 that is no multiple of the O6 in force, before the write (KAN-808)', async () => {
    const backend = render(plannerArea({ grid: '15' }), ['planning.grid_minutes', 'planning.slot_minutes'], [
      { method: 'PUT', match: /\/setting-values\/planning\.slot_minutes$/, handler: () => slot({ isSet: true, value: { number: '45' }, version: 1 }) },
    ])
    const betrieb = await rowCell(slotLabel(), 1)
    expect(betrieb).toHaveTextContent(i18n.t('settings.rule.multipleOf', { setting: gridLabel(), values: '15' }))
    await openEditor(betrieb)
    const input = await screen.findByRole('textbox', { name: `${slotLabel()} · Betrieb` })
    await userEvent.type(input, '40{Enter}')
    expect(
      await screen.findByText(
        i18n.t('settings.rule.refused.notAMultiple', {
          value: i18n.t('settings.value.withUnit', { number: '40', unit: i18n.t('settings.units.minutes') }),
          bound: '15',
          setting: gridLabel(),
        }),
      ),
    ).toBeInTheDocument()
    expect(backend.calls.filter((call) => call.method === 'PUT')).toEqual([])
    await userEvent.clear(input)
    await userEvent.type(input, '45{Enter}')
    await waitFor(() => expect(backend.calls.filter((call) => call.method === 'PUT')).toHaveLength(1))
    expect(backend.calls.find((call) => call.method === 'PUT')!.body).toEqual({ value: { number: '45' } })
  })

  it('a location with its own O6 hints and checks its O7 against that one (KAN-808)', async () => {
    render(plannerArea({ grid: '15', locationGrid: '20' }), ['planning.grid_minutes', 'planning.slot_minutes'])
    const hauptsitz = await rowCell(slotLabel(), 2)
    const filiale = await rowCell(slotLabel(), 3)
    expect(hauptsitz).toHaveTextContent(i18n.t('settings.rule.multipleOf', { setting: gridLabel(), values: '20' }))
    expect(filiale).toHaveTextContent(i18n.t('settings.rule.multipleOf', { setting: gridLabel(), values: '15' }))
    // The Betrieb's O7 is in force at both locations, neither holding its own: both grids count.
    const betrieb = await rowCell(slotLabel(), 1)
    expect(betrieb).toHaveTextContent(i18n.t('settings.rule.multipleOf', { setting: gridLabel(), values: '15, 20' }))
  })
})
