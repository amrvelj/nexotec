// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomersListPage } from './CustomersListPage'

// WP-6c exit criterion: "column config survives a reload and a different
// browser". The persistence path is exercised end to end — the transport
// is stubbed at `fetch`, never `useGridPreferences`/`usePersistedPreference`
// — so this proves the GET/PUT `/v1/me/preferences/grid:<key>` round trip,
// not just the reducer under it.

const GRID_PREF_PATH = /\/me\/preferences\/grid:mdm\.customers\.list$/
const languageColumn = () => new RegExp(i18n.t('customersList.columns.language'), 'i')

function customersRoute(): FakeRoute {
  return { match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1' })]) }
}

describe('CustomersListPage — column configuration survives a reload', () => {
  it('hiding a column persists to /v1/me/preferences and is restored on a fresh mount', async () => {
    const user = userEvent.setup()

    // --- First session -----------------------------------------------------
    let storedGridPayload: unknown = {}
    const backend = installFakeBackend([
      customersRoute(),
      { method: 'GET', match: GRID_PREF_PATH, handler: () => ({ payload: storedGridPayload }) },
      {
        method: 'PUT',
        match: GRID_PREF_PATH,
        handler: (req) => {
          storedGridPayload = req.body
          return { ok: true }
        },
      },
    ])

    const first = renderWithProviders(<CustomersListPage />, { route: '/customers' })

    // The "Language" column is visible to begin with.
    expect(await screen.findByRole('columnheader', { name: languageColumn() })).toBeInTheDocument()

    // Open the column panel and hide it.
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    const panelCheckbox = await screen.findByRole('checkbox', { name: languageColumn() })
    expect(panelCheckbox).toBeChecked()
    await user.click(panelCheckbox)

    // The grid drops the column immediately …
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: languageColumn() })).not.toBeInTheDocument())

    // … and the change reaches the server (debounced PUT) and the local mirror.
    await waitFor(() => expect(backend.callsTo(GRID_PREF_PATH, 'PUT').length).toBeGreaterThan(0), { timeout: 2000 })
    const put = backend.callsTo(GRID_PREF_PATH, 'PUT').at(-1)!
    expect((put.body as { columnLayout: { hidden: string[] } }).columnLayout.hidden).toContain('language')
    expect(window.localStorage.getItem('dms.preferences.grid.mdm.customers.list')).toContain('"hidden"')

    first.unmount()

    // --- Fresh session: new query client, cleared local mirror ------------
    window.localStorage.clear()
    expect(storedGridPayload).toMatchObject({ columnLayout: { hidden: expect.arrayContaining(['language']) } })

    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    // The grid comes back without the hidden column — restored from the
    // server alone, since the mirror was wiped (the "different browser" case).
    expect(await screen.findByRole('columnheader', { name: new RegExp(i18n.t('customersList.columns.name'), 'i') })).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByRole('columnheader', { name: languageColumn() })).not.toBeInTheDocument(),
    )
  })
})
