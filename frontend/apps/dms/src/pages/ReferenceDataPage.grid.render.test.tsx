// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { semantic } from '@nexotec/ui-kit'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// jsdom has no layout, so TanStack Virtual would window the grid down to zero
// rows — render every row instead.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

// KAN-516 (Anto, 2026-11-13, option C) — Reference data is the kit's grid:
// sorted in the browser (the endpoint takes no sort), the sort in the URL,
// the Columns panel (ADR-060), a row missing a language tinted as a whole,
// and the row menu (ADR-061) offering platform staff the value's labels —
// a reader is offered no action, so no menu (KAN-430).

const value = (valueCode: string, labelDe: string, overrides: Record<string, unknown> = {}) => ({
  id: `v-${valueCode}`,
  listCode: 'fuel_type',
  valueCode,
  labelDe,
  labelFr: `${labelDe} (fr)`,
  labelIt: `${labelDe} (it)`,
  labelEn: `${labelDe} (en)`,
  active: true,
  sortOrder: 0,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-02T00:00:00Z',
  createdBy: null,
  updatedBy: null,
  ...overrides,
})

const VALUES = [value('petrol', 'Benzin', { labelIt: '' }), value('diesel', 'Diesel', { version: 4 }), value('electric', 'Elektrisch')]

function session(writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Pia', lastName: 'Plattform', email: 'pia@nexotec.ch', accessRoles: ['platform_admin'], isDealerManager: false },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function install(writable: string[], extra: FakeRoute[] = []) {
  return installFakeBackend([
    session(writable),
    ...extra,
    { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => ({ items: VALUES, nextCursor: null }) },
  ])
}

const codesShown = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((line) => within(line).getAllByRole('cell')[0].textContent)

afterEach(cleanup)

describe('ReferenceDataPage — the kit grid (KAN-516)', () => {
  it('sorts the values in the browser and reads a pasted sort from the URL', async () => {
    const user = userEvent.setup()
    const backend = install(['customers'])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference?sort=code:desc' })

    const table = await screen.findByRole('table', { name: 'Werte der Referenzliste' })
    await within(table).findByText('petrol')
    expect(codesShown(table)).toEqual(['petrol', 'electric', 'diesel'])
    const reads = backend.callsTo(/\/reference-data\/fuel_type$/).length

    const german = within(table).getByRole('columnheader', { name: 'Deutsch' })
    await user.click(german)
    expect(german).toHaveAttribute('aria-sort', 'ascending')
    expect(codesShown(table)).toEqual(['petrol', 'diesel', 'electric'])
    expect(backend.callsTo(/\/reference-data\/fuel_type$/)).toHaveLength(reads)
  })

  it('tints a value missing a language as a whole row', async () => {
    install(['customers'])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const table = await screen.findByRole('table', { name: 'Werte der Referenzliste' })
    const rowOf = (code: string) => within(table).getByText(code).closest('[role="row"]') as HTMLElement
    expect(rowOf('petrol').style.backgroundColor).toBe(semantic.destructive.surface)
    expect(rowOf('diesel').style.backgroundColor).toBe('')
  })

  it('offers every stored field in the Columns panel, the bookkeeping ones hidden by default (ADR-060)', async () => {
    const user = userEvent.setup()
    install(['customers'])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const table = await screen.findByRole('table', { name: 'Werte der Referenzliste' })
    await within(table).findByText('petrol')
    expect(within(table).queryByRole('columnheader', { name: 'Reihenfolge' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Spalten' }))
    for (const name of ['Aktiv', 'Reihenfolge', 'Erstellt', 'Zuletzt geändert']) {
      expect(await screen.findByRole('checkbox', { name })).toBeInTheDocument()
    }
    await user.click(screen.getByRole('checkbox', { name: 'Reihenfolge' }))
    expect(await within(table).findByRole('columnheader', { name: 'Reihenfolge' })).toBeInTheDocument()
  })

  it('edits a value’s labels from its row menu: only what changed, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = install(
      ['reference_lists'],
      [
        {
          method: 'PATCH',
          match: /\/reference-data\/fuel_type\/diesel$/,
          handler: (req) => ({ ...VALUES[1], ...(req.body as object), version: 5 }),
        },
      ],
    )
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const table = await screen.findByRole('table', { name: 'Werte der Referenzliste' })
    const line = (await within(table).findByText('diesel')).closest('[role="row"]') as HTMLElement
    await user.click(within(line).getByRole('button', { name: 'Zeilenaktionen' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Bezeichnungen bearbeiten' }))

    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByLabelText('Code', { exact: false })).toHaveAttribute('readonly')
    const italian = dialog.getByLabelText('Italiano', { exact: false })
    await user.clear(italian)
    await user.type(italian, 'Gasolio')
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/reference-data\/fuel_type\/diesel$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/reference-data\/fuel_type\/diesel$/, 'PATCH')
    expect(patch.body).toEqual({ labelIt: 'Gasolio' })
    expect(patch.headers.get('If-Match')).toBe('4')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(within(table).getByText('Gasolio')).toBeInTheDocument()
  })

  it('offers a reader no row menu: there is nothing a reader can do to a value', async () => {
    install(['customers'])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const table = await screen.findByRole('table', { name: 'Werte der Referenzliste' })
    await within(table).findByText('petrol')
    expect(within(table).queryByRole('button', { name: 'Zeilenaktionen' })).toBeNull()
  })
})
