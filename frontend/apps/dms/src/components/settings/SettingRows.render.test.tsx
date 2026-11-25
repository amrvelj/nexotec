// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES } from '../../test/fakeBackend'
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

function render(data: AreaSettingsRead, keys?: string[]) {
  installFakeBackend([
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
})
