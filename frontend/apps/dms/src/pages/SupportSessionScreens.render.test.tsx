// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { PlatformUsersPage } from './PlatformUsersPage'
import { SupportSessionLogPage } from './SupportSessionLogPage'

// KAN-221 PR-6 — the support-session log (the manager's, FR-A-15) and the
// Platform users panel (staff, grant and revoke; ADR-188).

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

afterEach(() => cleanup())

function entry(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ss-1',
    staffUserId: 'staff-1',
    targetUserId: 'user-2',
    dealershipId: 'd-1',
    reason: 'Die Offerte druckt das Logo nicht — Ticket 4712.',
    startedAt: '2026-11-20T09:00:00Z',
    expiresAt: '2026-11-20T10:00:00Z',
    endedAt: '2026-11-20T09:20:00Z',
    endedBy: 'user',
    active: false,
    staffName: 'Petra Support',
    targetUserName: 'Beat Verkauf',
    dealershipName: 'Garage Nord AG',
    ...overrides,
  }
}

function staff(overrides: Record<string, unknown> = {}) {
  return {
    id: 'staff-2',
    firstName: 'Marco',
    lastName: 'Hilfe',
    email: 'marco@nexotec.example',
    status: 'active',
    version: 4,
    homeDealershipId: 'platform',
    homeDealershipName: 'Nexotec',
    inPlatformTenant: true,
    mfaEnrolled: true,
    runningSupportSessionId: null,
    ...overrides,
  }
}

describe('SupportSessionLogPage (manager)', () => {
  it("reads the active Betrieb's log newest first and says how each session ended", async () => {
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/dealerships\/[^/]+\/support-sessions$/,
        handler: () => ({
          items: [entry({ id: 'ss-2', active: true, endedAt: null, endedBy: null }), entry()],
          nextCursor: null,
        }),
      },
    ])
    renderWithProviders(<SupportSessionLogPage />, { route: '/administration/support-sessions' })
    const grid = await screen.findByRole('table', { name: i18n.t('supportSessions.log.ariaLabel') })
    expect(await within(grid).findByText(i18n.t('supportSessions.log.state.running'))).toBeInTheDocument()
    expect(within(grid).getByText(i18n.t('supportSessions.log.state.user'))).toBeInTheDocument()
    expect(within(grid).getAllByText('Petra Support')).toHaveLength(2)
    const [call] = backend.callsTo(/\/support-sessions$/)
    expect(call.params.get('sort')).toBe('startedAt:desc')
  })

  it('says a refusal for a user who does not manage the Betrieb', async () => {
    installFakeBackend([
      { method: 'GET', match: /\/dealerships\/[^/]+\/support-sessions$/, handler: () => status(403, { error: { code: 'forbidden', message: 'no' } }) },
    ])
    renderWithProviders(<SupportSessionLogPage />, { route: '/administration/support-sessions' })
    expect(await screen.findByText(i18n.t('supportSessions.log.forbidden.title'))).toBeInTheDocument()
  })

  it('every session column but the names and the state sorts on the server', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /\/dealerships\/[^/]+\/support-sessions$/, handler: () => ({ items: [entry()], nextCursor: null }) },
    ])
    renderWithProviders(<SupportSessionLogPage />, { route: '/administration/support-sessions' })
    const grid = await screen.findByRole('table', { name: i18n.t('supportSessions.log.ariaLabel') })
    await within(grid).findByText('Petra Support')
    await userEvent.click(within(grid).getByText(i18n.t('supportSessions.log.columns.reason')))
    await vi.waitFor(() => expect(backend.callsTo(/\/support-sessions$/).at(-1)?.params.get('sort')).toBe('reason:asc'))
  })
})

const PLATFORM_ROUTES: FakeRoute[] = [
  {
    method: 'GET',
    match: /\/platform\/support-sessions$/,
    handler: () => ({ items: [entry()], nextCursor: null }),
  },
]

describe('PlatformUsersPage (staff)', () => {
  it('lists staff with the second factor, a legacy holder and a running session', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /\/platform\/staff$/,
        handler: () => ({
          items: [
            staff(),
            staff({ id: 'staff-3', firstName: 'Alt', lastName: 'Konto', inPlatformTenant: false, homeDealershipName: 'Garage Nord AG', mfaEnrolled: false, runningSupportSessionId: 'ss-9' }),
          ],
          nextCursor: null,
        }),
      },
      ...PLATFORM_ROUTES,
    ])
    renderWithProviders(<PlatformUsersPage />, { route: '/platform/users' })
    const grid = await screen.findByRole('table', { name: i18n.t('supportSessions.staff.ariaLabel') })
    expect(await within(grid).findByText('Marco Hilfe')).toBeInTheDocument()
    expect(within(grid).getByText(i18n.t('supportSessions.staff.home.legacy', { name: 'Garage Nord AG' }))).toBeInTheDocument()
    expect(within(grid).getByText(i18n.t('supportSessions.staff.mfa.no'))).toBeInTheDocument()
    expect(within(grid).getByText(i18n.t('supportSessions.staff.session.running'))).toBeInTheDocument()
    // Below: the log of every Betrieb, naming the Betrieb.
    const log = await screen.findByRole('table', { name: i18n.t('supportSessions.log.ariaLabel') })
    expect(await within(log).findByText('Garage Nord AG')).toBeInTheDocument()
  })

  it('grants access: a new staff account, invited by e-mail', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /\/platform\/staff$/, handler: () => ({ items: [staff()], nextCursor: null }) },
      { method: 'POST', match: /\/platform\/staff$/, handler: (req) => staff({ id: 'staff-9', ...(req.body as object), status: 'invited' }) },
      ...PLATFORM_ROUTES,
    ])
    renderWithProviders(<PlatformUsersPage />, { route: '/platform/users' })
    await userEvent.click(await screen.findByRole('button', { name: i18n.t('supportSessions.staff.grant') }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(new RegExp(i18n.t('supportSessions.staff.grantDialog.firstName'))), 'Neu')
    await userEvent.type(within(dialog).getByLabelText(new RegExp(i18n.t('supportSessions.staff.grantDialog.lastName'))), 'Staff')
    await userEvent.type(within(dialog).getByLabelText(new RegExp(`^${i18n.t('supportSessions.staff.grantDialog.email')}`)), 'neu@nexotec.example')
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('supportSessions.staff.grantDialog.submit') }))
    expect(await screen.findByText(i18n.t('supportSessions.staff.grantDialog.done', { email: 'neu@nexotec.example' }))).toBeInTheDocument()
    expect(backend.callsTo(/\/platform\/staff$/, 'POST')[0].body).toEqual({ firstName: 'Neu', lastName: 'Staff', email: 'neu@nexotec.example' })
  })

  it('revokes access with a reason and If-Match, and says a refusal in the UI language', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /\/platform\/staff$/, handler: () => ({ items: [staff()], nextCursor: null }) },
      {
        method: 'POST',
        match: /\/platform\/staff\/staff-2\/revoke$/,
        handler: () => status(422, { error: { code: 'unprocessable', message: 'x', details: { reason: 'last_platform_admin' } } }),
      },
      ...PLATFORM_ROUTES,
    ])
    renderWithProviders(<PlatformUsersPage />, { route: '/platform/users' })
    const grid = await screen.findByRole('table', { name: i18n.t('supportSessions.staff.ariaLabel') })
    await within(grid).findByText('Marco Hilfe')
    const [menu] = within(grid).getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await userEvent.click(menu)
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('supportSessions.staff.rowMenu.revoke') }))
    const dialog = await screen.findByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: i18n.t('supportSessions.staff.revokeDialog.submit') })
    expect(submit).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(new RegExp(i18n.t('supportSessions.staff.revokeDialog.reason'))), 'Austritt')
    await userEvent.click(submit)
    expect(await screen.findByText(i18n.t('supportSessions.staff.refusals.last_platform_admin'))).toBeInTheDocument()
    const [call] = backend.callsTo(/\/revoke$/, 'POST')
    expect(call.body).toEqual({ reason: 'Austritt' })
    expect(call.headers.get('If-Match')).toBe('4')
  })

  it('says a refusal for a user who is not staff', async () => {
    installFakeBackend([
      { method: 'GET', match: /\/platform\/staff$/, handler: () => status(403, { error: { code: 'forbidden', message: 'no' } }) },
      ...PLATFORM_ROUTES,
    ])
    renderWithProviders(<PlatformUsersPage />, { route: '/platform/users' })
    expect(await screen.findByText(i18n.t('supportSessions.staff.forbidden.title'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('supportSessions.staff.grant') })).not.toBeInTheDocument()
  })
})
