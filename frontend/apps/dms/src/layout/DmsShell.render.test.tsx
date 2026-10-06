// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-152 item 4 — CLAUDE.md i18n: no user-visible string is hardcoded.
// The sidebar's chrome (nav landmark, collapse toggle, notifications row,
// SOON badges, account menu, language and dealership-switch labels) was
// English in every UI language.

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const

const JSDOM_WIDTH = window.innerWidth

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
}

function renderShell(uiLanguage: (typeof LANGUAGES)[number], { wide = true } = {}) {
  // AppShell auto-collapses the sidebar below its breakpoint; jsdom's
  // default 1024px is below it.
  setViewportWidth(wide ? 1920 : 800)
  installFakeBackend([
    {
      method: 'GET',
      match: /\/me\/preferences\/ui$/,
      handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage, density: 'default' } }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: {
          id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
          phone: null, role: 'Advisor', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'employed',
          authIdentityId: 'auth-1', status: 'active', version: 1,
          createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
        activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
        memberships: [
          { id: 'd-1', legalName: 'Garage Nord AG' },
          { id: 'd-2', legalName: 'Garage Süd AG' },
        ],
      }),
    },
  ])
  return renderWithProviders(
    <DmsShell>
      <div />
    </DmsShell>,
  )
}

afterEach(async () => {
  setViewportWidth(JSDOM_WIDTH)
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell sidebar — every chrome string is translated (KAN-152)', () => {
  it.each(LANGUAGES)('renders the sidebar chrome in %s', async (lng) => {
    renderShell(lng)
    const tr = (key: string) => i18n.getFixedT(lng)(key)

    const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
    expect(await within(nav).findByText(tr('shell.sidebar.notifications'))).toBeInTheDocument()
    expect(within(nav).getAllByText(tr('shell.sidebar.soon')).length).toBeGreaterThan(0)
    expect(within(nav).getByRole('button', { name: tr('shell.sidebar.collapseSidebar') })).toBeInTheDocument()

    await userEvent.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
    expect(await screen.findByText(tr('shell.sidebar.switchDealership'))).toBeInTheDocument()
    expect(screen.getByText(tr('shell.signOut'))).toBeInTheDocument()
  })

  it.each(LANGUAGES)('collapsed: the account menu carries language and notifications in %s', async (lng) => {
    renderShell(lng, { wide: false })
    const tr = (key: string) => i18n.getFixedT(lng)(key)

    const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
    expect(await within(nav).findByRole('button', { name: tr('shell.sidebar.expandSidebar') })).toBeInTheDocument()

    await userEvent.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))
    expect(await screen.findByText(tr('shell.sidebar.language'))).toBeInTheDocument()
    expect(screen.getByText(tr('shell.sidebar.notificationsSoon'))).toBeInTheDocument()
  })

  it('shows no English chrome to a French user', async () => {
    renderShell('fr')

    const nav = await screen.findByRole('navigation', { name: 'Navigation principale' })
    expect(await within(nav).findByRole('button', { name: 'Replier la barre latérale' })).toBeInTheDocument()
    for (const english of ['SOON', 'Collapse sidebar', 'Account menu']) {
      expect(within(nav).queryByText(english)).not.toBeInTheDocument()
      expect(within(nav).queryByRole('button', { name: english })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })
})
