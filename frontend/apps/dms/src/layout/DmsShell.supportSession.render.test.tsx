// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { formatTime } from '../utils/format'
import { DmsShell } from './DmsShell'

// KAN-221 PR-6 (D-A-05, FR-A-18) — while a Nexotec staff member is in the
// account, every screen says who, why and until when, in the shell's one
// notice slot (ADR-084), with no dismiss; «Sitzung beenden» ends it as the
// user, and the next read finds none. The staff side (acting_for, PR 3)
// reads the same session from the other side.

const SESSION = {
  id: 'ss-1',
  viewer: 'user',
  staffName: 'Petra Support',
  targetUserName: 'Beat Verkauf',
  reason: 'Die Offerte druckt das Logo nicht — Ticket 4712.',
  startedAt: '2026-11-21T09:00:00Z',
  expiresAt: '2026-11-21T10:00:00Z',
}

function renderShell(routes: FakeRoute[]) {
  const backend = installFakeBackend(routes)
  renderWithProviders(
    <DmsShell>
      <div />
    </DmsShell>,
  )
  return backend
}

afterEach(async () => {
  vi.unstubAllEnvs()
  await i18n.changeLanguage('de')
})

describe('DmsShell support-session banner (KAN-221 PR-6)', () => {
  it('names the staff member, the reason and the end, outside the content, without a dismiss', async () => {
    renderShell([{ method: 'GET', match: /\/me\/support-session$/, handler: () => ({ session: SESSION }) }])
    const banner = await screen.findByRole('status')
    expect(banner).toHaveTextContent('Petra Support')
    expect(banner).toHaveTextContent(SESSION.reason)
    expect(banner).toHaveTextContent(formatTime(SESSION.expiresAt, 'de-CH'))
    expect(banner.textContent).not.toMatch(/MISSING I18N KEY/)
    expect(screen.getByRole('main')).not.toContainElement(banner)
    // The one control is «Sitzung beenden» — nothing closes the strip itself.
    expect(within(banner).getAllByRole('button').map((b) => b.textContent)).toEqual([i18n.t('supportSessions.banner.end')])
  })

  it('«Sitzung beenden» ends the session as the user and the banner goes', async () => {
    let running = true
    const backend = renderShell([
      { method: 'GET', match: /\/me\/support-session$/, handler: () => ({ session: running ? SESSION : null }) },
      {
        method: 'POST',
        match: /\/platform\/support-sessions\/ss-1\/end$/,
        handler: () => {
          running = false
          return { ...SESSION, active: false, endedBy: 'user' }
        },
      },
    ])
    const banner = await screen.findByRole('status')
    await userEvent.click(within(banner).getByRole('button', { name: i18n.t('supportSessions.banner.end') }))
    await vi.waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(backend.callsTo(/\/platform\/support-sessions\/ss-1\/end$/, 'POST')).toHaveLength(1)
  })

  it('asks as a background request, so an unattended tab still idles out', async () => {
    const backend = renderShell([])
    await screen.findByRole('navigation', { name: i18n.t('shell.sidebar.mainNavigation') })
    await vi.waitFor(() => expect(backend.callsTo(/\/me\/support-session$/).length).toBeGreaterThan(0))
    expect(backend.callsTo(/\/me\/support-session$/)[0].headers.get('X-DMS-Background')).toBe('poll')
  })

  it('shows nothing when no session runs', async () => {
    const backend = renderShell([])
    await vi.waitFor(() => expect(backend.callsTo(/\/me\/support-session$/).length).toBeGreaterThan(0))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('a refused ending is said in the UI language and the banner re-reads', async () => {
    renderShell([
      { method: 'GET', match: /\/me\/support-session$/, handler: () => ({ session: SESSION }) },
      {
        method: 'POST',
        match: /\/platform\/support-sessions\/ss-1\/end$/,
        handler: () => status(500, { error: { code: 'internal', message: 'boom' } }),
      },
    ])
    const banner = await screen.findByRole('status')
    await userEvent.click(within(banner).getByRole('button', { name: i18n.t('supportSessions.banner.end') }))
    expect(await screen.findByText(new RegExp(i18n.t('supportSessions.banner.endError')))).toBeInTheDocument()
  })

  it('the staff side names the user it acts for', async () => {
    renderShell([{ method: 'GET', match: /\/me\/support-session$/, handler: () => ({ session: { ...SESSION, viewer: 'staff' } }) }])
    const banner = await screen.findByRole('status')
    expect(banner).toHaveTextContent('Beat Verkauf')
    expect(banner).not.toHaveTextContent('Petra Support')
  })

  it('stacks above the deployment notice in the one slot when both apply', async () => {
    vi.stubEnv('VITE_OUTBOX_WORKER_ABSENT', 'true')
    renderShell([{ method: 'GET', match: /\/me\/support-session$/, handler: () => ({ session: SESSION }) }])
    await vi.waitFor(() => expect(screen.getAllByRole('status')).toHaveLength(2))
    const [first, second] = screen.getAllByRole('status')
    expect(first).toHaveTextContent('Petra Support')
    expect(second).toHaveTextContent(i18n.t('shell.environmentNotice.outboxWorkerAbsent'))
  })
})
