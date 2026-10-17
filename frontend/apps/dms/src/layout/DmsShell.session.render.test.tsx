// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import i18n from '../i18n'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-237 — a refused dealership switch did nothing visible (the shell
// fired it with `void` and nothing caught it), and a 401 from any API call
// left the client "signed in" on a screen whose every request now failed.

const USER = {
  id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
  phone: null, role: 'service_advisor', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'active',
  authIdentityId: 'auth-1', status: 'active', version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}
const NORD = { id: 'd-1', legalName: 'Garage Nord AG' }
const SUED = { id: 'd-2', legalName: 'Garage Süd AG' }

const JSDOM_WIDTH = window.innerWidth

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
}

function install(
  uiLanguage: string,
  routes: FakeRoute[],
  meResponses: Array<{ memberships: typeof NORD[]; entitlements: string[] }>,
  activeDealership: typeof NORD = NORD,
) {
  // AppShell auto-collapses the sidebar below its breakpoint.
  setViewportWidth(1920)
  let meCalls = 0
  return installFakeBackend([
    ...routes,
    {
      method: 'GET',
      match: /\/me\/preferences\/ui$/,
      handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage, density: 'default' } }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => {
        const response = meResponses[Math.min(meCalls, meResponses.length - 1)]
        meCalls += 1
        return { user: USER, activeDealership, memberships: response.memberships, entitlements: response.entitlements }
      },
    },
  ])
}

/** What the session says the active dealership is — the switch's effect,
 * read where every screen reads it. */
function ActiveDealershipProbe() {
  const { activeDealership } = useAuth()
  return <output aria-label="active-dealership">{activeDealership?.legalName ?? 'none'}</output>
}

afterEach(async () => {
  setViewportWidth(JSDOM_WIDTH)
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell — a refused dealership switch is said (KAN-237)', () => {
  it.each(['de', 'fr'] as const)(
    'shows the translated refusal and keeps the active dealership (%s)',
    async (lng) => {
      const user = userEvent.setup()
      const tr = i18n.getFixedT(lng)
      const backend = install(
        lng,
        [
          {
            method: 'POST',
            match: /\/auth\/switch-dealership$/,
            handler: () => status(403, {
              error: { code: 'forbidden', message: 'This dealership is not one of your memberships.', details: null },
            }),
          },
        ],
        // The membership was revoked after login: the re-read list no
        // longer carries it.
        [{ memberships: [NORD, SUED], entitlements: ALL_ENTITLEMENTS }, { memberships: [NORD], entitlements: ALL_ENTITLEMENTS }],
      )
      renderWithProviders(
        <DmsShell>
          <ActiveDealershipProbe />
        </DmsShell>,
      )

      const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
      await user.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
      await user.click(await screen.findByRole('menuitem', { name: SUED.legalName }))

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent(tr('shell.switchDealershipError.notAMember'))
      expect(alert).not.toHaveTextContent('This dealership is not one of your memberships.')
      expect(screen.getByLabelText('active-dealership')).toHaveTextContent(NORD.legalName)
      expect(backend.callsTo(/\/auth\/switch-dealership$/, 'POST')).toHaveLength(1)

      // The refused dealership is no longer offered: the list was re-read.
      await waitFor(() => expect(backend.callsTo(/\/auth\/me$/, 'GET')).toHaveLength(2))
      await user.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
      await screen.findByText(tr('shell.signOut'))
      expect(screen.queryByRole('menuitem', { name: SUED.legalName })).not.toBeInTheDocument()
      expect(screen.getByLabelText('active-dealership')).toHaveTextContent(NORD.legalName)
    },
  )

  it('a session still acting in a sister dealership whose membership was revoked can switch back home', async () => {
    // KAN-237 review: /auth/me reads memberships from the database, so the
    // revoked active dealership is no longer in the list — the switcher must
    // still offer the home dealership instead of vanishing.
    const user = userEvent.setup()
    const tr = i18n.getFixedT('de')
    const backend = install(
      'de',
      [
        {
          method: 'POST',
          match: /\/auth\/switch-dealership$/,
          handler: () => ({ user: USER, activeDealership: NORD, memberships: [NORD], entitlements: ALL_ENTITLEMENTS }),
        },
      ],
      [{ memberships: [NORD], entitlements: ALL_ENTITLEMENTS }],
      SUED,
    )
    renderWithProviders(
      <DmsShell>
        <ActiveDealershipProbe />
      </DmsShell>,
    )

    const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
    await user.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
    expect(await screen.findByRole('menuitem', { name: SUED.legalName })).toHaveAttribute('data-disabled', 'true')
    await user.click(screen.getByRole('menuitem', { name: NORD.legalName }))

    await waitFor(() => expect(backend.callsTo(/\/auth\/switch-dealership$/, 'POST')).toHaveLength(1))
  })

  it('a switch that fails for another reason says so too, and the alert can be dismissed', async () => {
    const user = userEvent.setup()
    install(
      'de',
      [{ method: 'POST', match: /\/auth\/switch-dealership$/, handler: () => status(503, null) }],
      [{ memberships: [NORD, SUED], entitlements: ALL_ENTITLEMENTS }],
    )
    renderWithProviders(
      <DmsShell>
        <ActiveDealershipProbe />
      </DmsShell>,
    )
    const tr = i18n.getFixedT('de')

    const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
    await user.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
    await user.click(await screen.findByRole('menuitem', { name: SUED.legalName }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(tr('shell.switchDealershipError.failed'))
    expect(screen.getByLabelText('active-dealership')).toHaveTextContent(NORD.legalName)

    await user.click(within(alert).getByRole('button', { name: tr('shell.switchDealershipError.dismiss') }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})

function SessionProbePage() {
  // Any screen's own request — here, the customer list the page would load.
  const query = useQuery({ queryKey: ['probe'], queryFn: () => api.get('/customers') })
  return <p>{query.isError ? 'request failed' : 'page'}</p>
}

describe('DmsShell — a 401 from any API call ends the client session (KAN-237)', () => {
  it('routes to the login screen', async () => {
    install(
      'de',
      [
        {
          method: 'GET',
          match: /^\/customers$/,
          handler: () => status(401, { error: { code: 'unauthorized', message: 'Not authenticated', details: null } }),
        },
      ],
      [{ memberships: [NORD], entitlements: ALL_ENTITLEMENTS }],
    )
    renderWithProviders(
      <Routes>
        {/* The login screen itself has its own render test
            (pages/LoginPage.render.test.tsx); this stands in for its route. */}
        <Route path="/login" element={<p>login screen</p>} />
        <Route
          path="/customers"
          element={
            <ProtectedRoute>
              <DmsShell>
                <SessionProbePage />
              </DmsShell>
            </ProtectedRoute>
          }
        />
      </Routes>,
      { route: '/customers' },
    )

    expect(await screen.findByText('login screen')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: i18n.t('shell.sidebar.mainNavigation') })).not.toBeInTheDocument()
  })

  it('a 401 to a signed-out visitor on the login screen does not loop', async () => {
    // /auth/me answers 401 — nobody is signed in.
    const backend = installFakeBackend([
      { method: 'GET', match: /\/auth\/me$/, handler: () => status(401, { error: { code: 'unauthorized', message: 'x', details: null } }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/login" element={<p>login screen</p>} />
      </Routes>,
      { route: '/login' },
    )

    expect(await screen.findByText('login screen')).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/auth\/me$/)).toHaveLength(1))
    // Give any re-render-driven refetch a chance to show up.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(backend.callsTo(/\/auth\/me$/)).toHaveLength(1)
  })
})
