// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-275 — ADR-081 (option D): Render's staging runs no outbox worker, so
// no event consumer and no nightly job runs there. The shell says so on
// every signed-in screen, in every UI language, when the build was made
// with VITE_OUTBOX_WORKER_ABSENT=true (render.yaml's web service); any
// other value, or none (docker-compose, CI, `npm run dev`), shows nothing.
// The strip lives outside the scrolling <main> (ADR-084) and AppShell
// publishes its height so OverviewShellRegion can subtract it.

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
const MARKER = /⚠ MISSING I18N KEY/
const KEY = 'shell.environmentNotice.outboxWorkerAbsent'

function renderShell(uiLanguage: (typeof LANGUAGES)[number]) {
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
          phone: null, role: 'service_advisor', accessRoles: ['sales'], isDealerManager: false,
          employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
          createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
        activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
        memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
      }),
    },
  ])
  return renderWithProviders(
    <DmsShell>
      <div />
    </DmsShell>,
  )
}

async function shellIsUp(lng: (typeof LANGUAGES)[number]) {
  await screen.findByRole('navigation', { name: i18n.getFixedT(lng)('shell.sidebar.mainNavigation') })
}

// jsdom lays nothing out, so offsetHeight is 0 unless stubbed: the height
// AppShell publishes as --shell-notice-height is asserted through a stub.
const NOTICE_PX = 25
const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')

afterEach(async () => {
  vi.unstubAllEnvs()
  if (offsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', offsetHeight)
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell environment notice — staging runs no outbox worker (KAN-275)', () => {
  it.each(LANGUAGES)('VITE_OUTBOX_WORKER_ABSENT=true shows the notice, translated, in %s', async (lng) => {
    vi.stubEnv('VITE_OUTBOX_WORKER_ABSENT', 'true')
    renderShell(lng)
    await shellIsUp(lng)

    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent(i18n.getFixedT(lng)(KEY))
    expect(notice.textContent).not.toMatch(MARKER)
    // Persistent: outside the scrolling content region, not inside it.
    expect(screen.getByRole('main')).not.toContainElement(notice)
  })

  it('publishes its measured height for the overview grids (--shell-notice-height)', async () => {
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => NOTICE_PX })
    vi.stubEnv('VITE_OUTBOX_WORKER_ABSENT', 'true')
    renderShell('de')
    await shellIsUp('de')
    const column = screen.getByRole('main').parentElement as HTMLElement
    await vi.waitFor(() => expect(column.style.getPropertyValue('--shell-notice-height')).toBe(`${NOTICE_PX}px`))
  })

  it('is off by default: no setting, no notice, 0px published', async () => {
    renderShell('de')
    await shellIsUp('de')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    const column = screen.getByRole('main').parentElement as HTMLElement
    expect(column.style.getPropertyValue('--shell-notice-height')).toBe('0px')
  })

  it('only the exact string "true" switches it on', async () => {
    vi.stubEnv('VITE_OUTBOX_WORKER_ABSENT', '1')
    renderShell('de')
    await shellIsUp('de')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('the four translations say different things (no bundle copies another)', () => {
    const texts = LANGUAGES.map((lng) => i18n.getFixedT(lng)(KEY))
    expect(new Set(texts).size).toBe(LANGUAGES.length)
    for (const text of texts) expect(text).not.toMatch(MARKER)
  })
})
