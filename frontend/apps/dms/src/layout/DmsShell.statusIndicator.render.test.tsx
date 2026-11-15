// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-223 (D-A-38; Anto, 2026-11-14, Q2 A): the status page's compact
// indicator sits on the «Status» entry and is the managers' only — a
// salesperson sees the entry (never role-hidden) but no indicator, and the
// shell never even asks for the page on their behalf.

const USER = {
  id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
  phone: null, role: 'sales', accessRoles: ['sales'], isDealerManager: false,
  employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

const page = (state: 'ok' | 'warning' | 'blocked', attentionCount: number) => ({
  dealershipId: 'd-1',
  state,
  attentionCount,
  items: [],
})

function renderShell({ manager, status }: { manager: boolean; status: ReturnType<typeof page> }) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1920 })
  const backend = installFakeBackend([
    {
      method: 'GET',
      match: /\/me\/preferences\/ui$/,
      handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage: 'de', density: 'default' } }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: USER,
        activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
        memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
        entitlements: ALL_ENTITLEMENTS,
        isActiveDealershipManager: manager,
      }),
    },
    { method: 'GET', match: /^\/dealerships\/d-1\/status$/, handler: () => status },
  ])
  renderWithProviders(
    <DmsShell>
      <div />
    </DmsShell>,
  )
  return backend
}

afterEach(async () => {
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell status indicator (KAN-223)', () => {
  const t = (key: string, options?: Record<string, unknown>) => i18n.getFixedT('de')(key, options)

  it('shows a manager the worst state with the count of items that want attention', async () => {
    renderShell({ manager: true, status: page('blocked', 2) })
    const nav = await screen.findByRole('navigation', { name: t('shell.sidebar.mainNavigation') })
    const indicator = await within(nav).findByRole('status', { name: t('statusPage.indicator.attention', { count: 2 }) })
    expect(indicator).toHaveAttribute('data-nav-indicator', 'destructive')
    expect(within(indicator).getByText('2')).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: new RegExp(`^${t('shell.nav.status')}`) })).toHaveAttribute(
      'href',
      '/administration/status',
    )
  })

  it('shows a manager a green dot when every item is fine', async () => {
    renderShell({ manager: true, status: page('ok', 0) })
    const nav = await screen.findByRole('navigation', { name: t('shell.sidebar.mainNavigation') })
    const indicator = await within(nav).findByRole('status', { name: t('statusPage.indicator.ok') })
    expect(indicator).toHaveAttribute('data-nav-indicator', 'success')
  })

  it('shows anyone else the entry but no indicator, and never asks for the page', async () => {
    const backend = renderShell({ manager: false, status: page('blocked', 2) })
    const nav = await screen.findByRole('navigation', { name: t('shell.sidebar.mainNavigation') })
    expect(await within(nav).findByText(t('shell.nav.status'))).toBeInTheDocument()
    await waitFor(() => expect(backend.calls.some((call) => call.pathname.endsWith('/auth/me'))).toBe(true))
    expect(within(nav).queryByRole('status')).not.toBeInTheDocument()
    expect(backend.calls.some((call) => call.pathname.endsWith('/status'))).toBe(false)
  })
})
