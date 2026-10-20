// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { UsersListPage } from './UsersListPage'

// KAN-200 — Dealer Administration › Users: the list shows each person's
// status (an expired invitation as such, FR-A-11), the manager flag and the
// last login; the row menu carries edit, resend, suspend and deactivate and
// never a delete (A-RULE-5); a non-manager is told the screen is the
// manager's (the API's 403), never shown an empty grid.

// jsdom has no layout, so TanStack Virtual windows down to zero rows; the
// repo's grid tests render every row through this mock.
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

const DEALERSHIP = 'dealership-1'

function user(overrides: Record<string, unknown>) {
  return {
    id: 'u-1',
    dealershipId: DEALERSHIP,
    firstName: 'Anna',
    lastName: 'Keller',
    email: 'anna@example.ch',
    phone: null,
    role: 'sales',
    accessRoles: ['sales', 'inventory'],
    isDealerManager: true,
    employmentStatus: 'active',
    authIdentityId: 'local:u-1',
    status: 'active',
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    updatedBy: null,
    directDial: null,
    employeeNumber: null,
    department: null,
    lineManagerUserId: null,
    salesAdvisorCode: null,
    commissionRelevant: false,
    validFrom: null,
    validTo: null,
    invitedAt: '2026-01-01T00:00:00Z',
    lastLoginAt: '2026-10-18T08:30:00Z',
    deactivatedAt: null,
    deactivationReason: null,
    hasSignatureImage: false,
    invitationExpiresAt: null,
    primaryLocationId: 'loc-1',
    additionalLocationIds: [],
    ...overrides,
  }
}

const user3 = (overrides: Record<string, unknown>) =>
  user({ id: 'u-3', firstName: 'Carla', lastName: 'Gross', email: 'carla@example.ch', isDealerManager: false, status: 'deactivated', deactivatedAt: '2026-09-30T10:00:00Z', deactivationReason: 'left', accessRoles: ['sales'], ...overrides })

const USERS = [
  user({}),
  user({ id: 'u-2', firstName: 'Beat', lastName: 'Frei', email: 'beat@example.ch', isDealerManager: false, status: 'invited', invitationExpiresAt: '2020-01-01T00:00:00Z', lastLoginAt: null, accessRoles: ['aftersales'] }),
  user3({}),
]

function routes() {
  return [
    { method: 'GET', match: /\/dealerships\/dealership-1\/users$/, handler: () => ({ items: USERS, nextCursor: null }) },
    { method: 'GET', match: /\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [{ id: 'loc-1', name: 'Zürich Nord', addressLocality: 'Zürich' }] }) },
  ]
}

describe('UsersListPage (KAN-200)', () => {
  it('lists the people with their status, manager flag, location and last login', async () => {
    installFakeBackend(routes())
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })

    expect(await screen.findByText('Anna Keller')).toBeInTheDocument()
    expect(screen.getByText('Beat Frei')).toBeInTheDocument()
    expect(screen.getByText('Carla Gross')).toBeInTheDocument()
    // Status, German UI: an invitation past its expiry reads as such.
    const grid = within(screen.getByRole('table'))
    expect(grid.getByText('Aktiv')).toBeInTheDocument()
    expect(grid.getByText('Einladung abgelaufen')).toBeInTheDocument()
    expect(grid.getByText('Deaktiviert')).toBeInTheDocument()
    // The manager flag, once; access shows the primary role plus the count.
    expect(screen.getAllByText('Manager')).toHaveLength(2) // the column header and Anna's badge
    expect(screen.getByText('Verkauf +1')).toBeInTheDocument()
    expect(screen.getByText('Aftersales')).toBeInTheDocument()
    expect(grid.getAllByText('Zürich Nord')).toHaveLength(3)
  })

  it('offers edit, resend, suspend and deactivate in the row menu and no delete', async () => {
    const user = userEvent.setup()
    installFakeBackend(routes())
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    await screen.findByText('Beat Frei')

    const menus = screen.getAllByRole('button', { name: 'Zeilenaktionen' })
    await user.click(menus[1]) // Beat, invited
    const menu = await screen.findByRole('menu')
    expect(within(menu).getByText('Bearbeiten')).toBeInTheDocument()
    expect(within(menu).getByText('Einladung erneut senden')).toBeInTheDocument()
    // The lifecycle (Users → Lifecycle): an invited user is never activated
    // or suspended by hand — the entry stays, disabled, and says why.
    expect(within(menu).getByText('Sperren')).toBeInTheDocument()
    expect(within(menu).getByText('Nur ein aktiver Benutzer kann gesperrt werden')).toBeInTheDocument()
    expect(within(menu).queryByText('Reaktivieren')).not.toBeInTheDocument()
    expect(within(menu).getByText('Offene Vorgänge übergeben')).toBeInTheDocument()
    expect(within(menu).getByText('Deaktivieren')).toBeInTheDocument()
    expect(within(menu).queryByText(/löschen/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/delete/i)).not.toBeInTheDocument()
  })

  it('reactivates a deactivated user and opens the hand-over for what they still own', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes(),
      { method: 'PATCH', match: /\/users\/u-3$/, handler: () => user3({}) },
      { method: 'GET', match: /\/users\/u-3\/ownership$/, handler: () => ({ groups: [], modules: ['customer', 'sales'] }) },
    ])
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    await screen.findByText('Carla Gross')

    await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[2]) // Carla, deactivated
    const menu = await screen.findByRole('menu')
    expect(within(menu).queryByText('Sperren')).not.toBeInTheDocument()
    await user.click(within(menu).getByText('Reaktivieren'))
    await waitFor(() => expect(backend.callsTo(/\/users\/u-3$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/users\/u-3$/, 'PATCH')[0].body).toEqual({ status: 'active' })

    await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[2])
    await user.click(await screen.findByText('Offene Vorgänge übergeben'))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('Offene Vorgänge von Carla Gross übergeben')).toBeInTheDocument()
  })

  it('says which hand-over a module failed after a deactivation, with the retry at hand', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      ...routes(),
      { method: 'GET', match: /\/users\/u-2\/ownership$/, handler: () => ({ groups: [{ module: 'sales', group: 'offers', count: 1 }], modules: ['sales'] }) },
      {
        method: 'POST',
        match: /\/users\/u-2\/deactivate$/,
        handler: () => ({
          user: { ...USERS[1], status: 'deactivated', version: 2 },
          transfers: [],
          failed: [{ module: 'sales', group: 'offers', error: 'sales is down' }],
        }),
      },
    ])
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    await screen.findByText('Beat Frei')
    await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[1])
    await user.click(await screen.findByText('Deaktivieren'))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/^Grund/), 'weg')
    await user.click(await dialog.findByRole('textbox', { name: 'Übergeben: Offene Offerten' }))
    await user.click(await screen.findByRole('option', { name: 'Nicht zuweisen (Warteschlange des Moduls)' }))
    await user.click(dialog.getByRole('button', { name: 'Deaktivieren' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('nicht alles konnte übergeben werden')
    expect(alert).toHaveTextContent('Offene Offerten (sales is down)')
    expect(alert).toHaveTextContent('Offene Vorgänge übergeben')
  })

  it('loads every colleague, page by page, for the pickers', async () => {
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/dealerships\/dealership-1\/users$/,
        handler: (req) => (req.params.get('cursor') === 'page-2' ? { items: [USERS[2]], nextCursor: null } : { items: [USERS[0], USERS[1]], nextCursor: 'page-2' }),
      },
      { method: 'GET', match: /\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [] }) },
    ])
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    await screen.findByText('Anna Keller')
    await waitFor(() => {
      const pages = backend.callsTo(/\/dealerships\/dealership-1\/users$/).filter((c) => c.params.get('limit') === '100')
      expect(pages.map((c) => c.params.get('cursor'))).toEqual([null, 'page-2'])
    })
  })

  it('sorts server-side from the URL and the column header', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend(routes())
    renderWithProviders(<UsersListPage />, { route: '/administration/users?sort=lastLoginAt:desc' })
    await screen.findByText('Anna Keller')
    expect(backend.callsTo(/\/dealerships\/dealership-1\/users$/)[0].params.get('sort')).toBe('lastLoginAt:desc')

    await user.click(screen.getByRole('columnheader', { name: /Name/ }))
    await waitFor(() => {
      const calls = backend.callsTo(/\/dealerships\/dealership-1\/users$/)
      expect(calls[calls.length - 1].params.get('sort')).toBe('lastName:asc')
    })
  })

  it('resends an invitation from the row menu with an Idempotency-Key', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes(),
      { method: 'POST', match: /\/users\/u-2\/invitations$/, handler: () => status(202) },
    ])
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    await screen.findByText('Beat Frei')

    await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[1])
    await user.click(await screen.findByText('Einladung erneut senden'))
    await waitFor(() => expect(backend.callsTo(/\/users\/u-2\/invitations$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/users\/u-2\/invitations$/, 'POST')[0].headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('filters by status through the URL and the search box', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend(routes())
    renderWithProviders(<UsersListPage />, { route: '/administration/users?status=invited' })
    await screen.findByText('Anna Keller')
    expect(backend.callsTo(/\/dealerships\/dealership-1\/users$/)[0].params.get('status')).toBe('invited')

    await user.type(screen.getByPlaceholderText('Name oder E-Mail …'), 'kel')
    await waitFor(() => {
      const calls = backend.callsTo(/\/dealerships\/dealership-1\/users$/)
      expect(calls[calls.length - 1].params.get('q')).toBe('kel')
    })
  })

  it('tells a non-manager that the screen is the manager’s instead of an empty grid', async () => {
    installFakeBackend([
      { method: 'GET', match: /\/dealerships\/dealership-1\/users$/, handler: () => status(403, { error: { code: 'forbidden', message: 'no', details: null } }) },
    ])
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    expect(await screen.findByText('Nur für Manager')).toBeInTheDocument()
    for (const button of screen.getAllByRole('button', { name: 'Benutzer einladen' })) expect(button).toBeDisabled()
  })
})
