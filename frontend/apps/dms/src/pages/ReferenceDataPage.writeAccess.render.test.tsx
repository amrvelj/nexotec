// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// KAN-516: the values are the kit's grid; jsdom has no layout, so TanStack
// Virtual would window it down to zero rows — render every row instead.
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

// KAN-471 — the reference lists are platform staff's to write (the PRD's
// "Reference lists" row since KAN-514; POST and PATCH answer 403 to
// everyone else, a dealer manager included). A dealer user used to be offered
// «Neuer Wert» and the inline edits anyway, fill the four-language form and
// then be refused. KAN-430's rule: the session's `writableCapabilities` says
// which rows the user may write, and an action outside it is hidden — the
// data stays readable.

const DIESEL = {
  id: 'v-diesel',
  listCode: 'fuel_type',
  valueCode: 'diesel',
  labelDe: 'Diesel',
  labelFr: 'Gazole',
  labelIt: 'Gasolio',
  labelEn: 'Diesel fuel',
  active: true,
  sortOrder: 0,
  version: 1,
}

function session(accessRoles: string[], isDealerManager: boolean, writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Mia', lastName: 'Muster', email: 'mia@example.ch', accessRoles, isDealerManager },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function install(sessionRoute: FakeRoute) {
  return installFakeBackend([
    sessionRoute,
    { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => ({ items: [DIESEL], nextCursor: null }) },
  ])
}

afterEach(cleanup)

describe('ReferenceDataPage — who is offered the writes (KAN-471)', () => {
  it('shows a dealer manager the list read-only: no «Neuer Wert», no inline edit, every label visible', async () => {
    const user = userEvent.setup()
    // A manager writes every row of their Betrieb but the platform-only ones,
    // so the server's list never holds reference_lists for them.
    const backend = install(session(['sales'], true, ['configurations', 'customers', 'stock_items']))

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    expect(await screen.findByText('Gazole')).toBeInTheDocument()
    for (const label of ['Diesel', 'Gasolio', 'Diesel fuel']) expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(i18n.t('referenceData.readOnly'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('referenceData.create.trigger') })).not.toBeInTheDocument()

    await user.click(screen.getByText('Gazole'))

    expect(screen.queryByDisplayValue('Gazole')).not.toBeInTheDocument()
    expect(backend.callsTo(/\/reference-data\//, 'PATCH')).toHaveLength(0)
    expect(backend.callsTo(/\/reference-data\//, 'POST')).toHaveLength(0)
  })

  it('tells a reader a language is missing without speaking of saving', async () => {
    installFakeBackend([
      session(['sales'], true, ['customers']),
      {
        method: 'GET',
        match: /\/reference-data\/fuel_type$/,
        handler: () => ({ items: [{ ...DIESEL, labelIt: '' }], nextCursor: null }),
      },
    ])

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    expect(await screen.findByLabelText(i18n.t('referenceData.missingTipReadOnly'))).toBeInTheDocument()
    expect(screen.queryByLabelText(i18n.t('referenceData.missingTip'))).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('referenceData.required'))).toBeInTheDocument()
  })

  it('keeps «Neuer Wert» and the row menu for platform staff; a cell edits nothing (KAN-516)', async () => {
    const user = userEvent.setup()
    install(session(['platform_admin'], false, ['reference_lists', 'configurations', 'customers']))

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    expect(await screen.findByRole('button', { name: i18n.t('referenceData.create.trigger') })).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('referenceData.readOnly'))).not.toBeInTheDocument()

    await user.click(await screen.findByText('Gazole'))

    // A grid row has a fixed height: labels are edited in the row menu's dialog, never in the cell.
    expect(screen.queryByDisplayValue('Gazole')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Zeilenaktionen' }).length).toBeGreaterThan(0)
  })
})
