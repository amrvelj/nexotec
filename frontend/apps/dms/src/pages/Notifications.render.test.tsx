// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { NotificationPreferenceRead, NotificationRead } from '../api/types'
import { NotificationsPage } from './NotificationsPage'
import { AccountLayout } from './account/AccountLayout'
import { AccountNotificationsPage } from './account/AccountNotificationsPage'

// KAN-216 — the inbox (FR-A-50: every notification, read/unread, a link to
// its subject) and the preferences in My account (FR-A-48: the user decides,
// security is locked), rendered from the bundles in the UI language.

function notification(overrides: Partial<NotificationRead>): NotificationRead {
  return {
    id: 'n-1',
    eventCode: 'security.new_device_login',
    family: 'security',
    titleKey: 'notifications.events.newDeviceLogin.title',
    bodyKey: 'notifications.events.newDeviceLogin.body',
    params: {},
    subjectType: 'user',
    subjectId: 'user-1',
    link: '/account/security',
    createdAt: '2026-10-31T08:00:00Z',
    deliveredAt: '2026-10-31T08:00:00Z',
    readAt: null,
    ...overrides,
  }
}

const UNREAD = notification({ id: 'n-1' })
const RECOVERY = notification({
  id: 'n-2',
  eventCode: 'security.platform_recovery_action',
  titleKey: 'notifications.events.platformRecoveryAction.title',
  bodyKey: 'notifications.events.platformRecoveryAction.body',
  params: { action: 'mfaReset' },
  readAt: '2026-10-31T09:00:00Z',
})

function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function renderInbox(routes: FakeRoute[], route = '/notifications') {
  const backend = installFakeBackend(routes)
  renderWithProviders(
    <Routes>
      <Route path="/notifications" element={<NotificationsPage />} />
      <Route path="/account/security" element={<Location />} />
    </Routes>,
    { route },
  )
  return backend
}

describe('the inbox (KAN-216)', () => {
  it('lists each notification in the UI language, the unread one marked', async () => {
    renderInbox([
      { method: 'GET', match: /\/me\/notifications$/, handler: () => ({ items: [UNREAD, RECOVERY], nextCursor: null }) },
    ])
    const unread = await screen.findByRole('listitem', { name: 'Anmeldung von einem neuen Gerät' })
    expect(within(unread).getByText('Ungelesen')).toBeInTheDocument()
    expect(within(unread).getByRole('button', { name: 'Als gelesen markieren' })).toBeInTheDocument()
    const recovery = screen.getByRole('listitem', { name: 'Wiederherstellung durch den Nexotec-Support' })
    // The recovery action is a code the bundle names — never the stated reason.
    expect(
      within(recovery).getByText(
        'Der Nexotec-Support hat an Ihrem Konto eine Wiederherstellung vorgenommen: zweiter Faktor zurückgesetzt.',
      ),
    ).toBeInTheDocument()
    expect(within(recovery).queryByText('Ungelesen')).not.toBeInTheDocument()
    expect(within(recovery).getByRole('button', { name: 'Als ungelesen markieren' })).toBeInTheDocument()
    expect(screen.getByText('Benachrichtigungen bleiben 90 Tage erhalten.')).toBeInTheDocument()
  })

  it('marks one read, one unread again, and all read', async () => {
    const user = userEvent.setup()
    const backend = renderInbox([
      { method: 'GET', match: /\/me\/notifications$/, handler: () => ({ items: [UNREAD, RECOVERY], nextCursor: null }) },
      { method: 'PATCH', match: /\/me\/notifications\/n-\d$/, handler: (req) => ({ ...UNREAD, ...(req.body as object) }) },
      { method: 'POST', match: /\/me\/notifications\/read-all$/, handler: () => ({ marked: 1 }) },
    ])
    const unread = await screen.findByRole('listitem', { name: 'Anmeldung von einem neuen Gerät' })
    await user.click(within(unread).getByRole('button', { name: 'Als gelesen markieren' }))
    await waitFor(() => expect(backend.callsTo(/\/me\/notifications\/n-1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/me\/notifications\/n-1$/, 'PATCH')[0].body).toEqual({ read: true })

    const recovery = screen.getByRole('listitem', { name: 'Wiederherstellung durch den Nexotec-Support' })
    await user.click(within(recovery).getByRole('button', { name: 'Als ungelesen markieren' }))
    await waitFor(() => expect(backend.callsTo(/\/me\/notifications\/n-2$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/me\/notifications\/n-2$/, 'PATCH')[0].body).toEqual({ read: false })

    await user.click(screen.getByRole('button', { name: 'Alle als gelesen markieren' }))
    await waitFor(() => expect(backend.callsTo(/\/me\/notifications\/read-all$/, 'POST')).toHaveLength(1))
  })

  it('opening one marks it read and goes to its subject', async () => {
    const user = userEvent.setup()
    const backend = renderInbox([
      { method: 'GET', match: /\/me\/notifications$/, handler: () => ({ items: [UNREAD], nextCursor: null }) },
      { method: 'PATCH', match: /\/me\/notifications\/n-1$/, handler: () => ({ ...UNREAD, readAt: '2026-10-31T10:00:00Z' }) },
    ])
    const item = await screen.findByRole('listitem', { name: 'Anmeldung von einem neuen Gerät' })
    await user.click(within(item).getByRole('button', { name: 'Öffnen' }))
    expect(await screen.findByTestId('location')).toHaveTextContent('/account/security')
    await waitFor(() => expect(backend.callsTo(/\/me\/notifications\/n-1$/, 'PATCH')).toHaveLength(1))
  })

  it('the unread filter is in the URL and asks the API for unread ones only', async () => {
    const backend = renderInbox(
      [{ method: 'GET', match: /\/me\/notifications$/, handler: () => ({ items: [], nextCursor: null }) }],
      '/notifications?filter=unread',
    )
    expect(await screen.findByText('Keine ungelesenen Benachrichtigungen.')).toBeInTheDocument()
    expect(backend.callsTo(/\/me\/notifications$/)[0].params.get('unread')).toBe('true')
  })

  it('says what will appear when the inbox is empty', async () => {
    renderInbox([{ method: 'GET', match: /\/me\/notifications$/, handler: () => ({ items: [], nextCursor: null }) }])
    expect(await screen.findByText('Keine Benachrichtigungen')).toBeInTheDocument()
  })

  it('loads the next page with the cursor', async () => {
    const user = userEvent.setup()
    const backend = renderInbox([
      {
        method: 'GET',
        match: /\/me\/notifications$/,
        handler: (req) =>
          req.params.get('cursor')
            ? { items: [RECOVERY], nextCursor: null }
            : { items: [UNREAD], nextCursor: 'c-1' },
      },
    ])
    await user.click(await screen.findByRole('button', { name: 'Weitere laden' }))
    expect(await screen.findByRole('listitem', { name: 'Wiederherstellung durch den Nexotec-Support' })).toBeInTheDocument()
    expect(backend.callsTo(/\/me\/notifications$/)[1].params.get('cursor')).toBe('c-1')
  })
})

const PREFERENCES: NotificationPreferenceRead[] = [
  { family: 'security', timing: 'immediate', inApp: true, push: true, switchable: false, hasEvents: true, version: 0 },
  { family: 'administration', timing: 'immediate', inApp: true, push: true, switchable: true, hasEvents: false, version: 0 },
  { family: 'assignment', timing: 'immediate', inApp: true, push: true, switchable: true, hasEvents: false, version: 0 },
]

function renderPreferences(routes: FakeRoute[]) {
  // The test's own routes first: the first match wins.
  const backend = installFakeBackend([
    ...routes,
    { method: 'GET', match: /\/me\/notification-preferences$/, handler: () => ({ items: PREFERENCES }) },
    { method: 'GET', match: /\/me\/push\/config$/, handler: () => ({ available: false, vapidPublicKey: null }) },
  ])
  renderWithProviders(
    <Routes>
      <Route path="/account" element={<AccountLayout />}>
        <Route path="notifications" element={<AccountNotificationsPage />} />
      </Route>
    </Routes>,
    { route: '/account/notifications' },
  )
  return backend
}

describe('notification preferences in My account (KAN-216)', () => {
  it('is its own tab of My account', async () => {
    renderPreferences([])
    expect(await screen.findByRole('tab', { name: 'Benachrichtigungen' })).toHaveAttribute('aria-selected', 'true')
  })

  it('locks the security family: always immediate, push on, not switchable', async () => {
    renderPreferences([])
    const timing = await screen.findByRole('textbox', { name: 'Zustellung – Sicherheit' })
    expect(timing).toBeDisabled()
    expect(timing).toHaveValue('Sofort')
    expect(screen.getByRole('switch', { name: 'Push – Sicherheit' })).toBeDisabled()
    expect(
      screen.getByText(
        'Sicherheitsmeldungen kommen immer sofort, im Posteingang und als Push, und lassen sich nicht abschalten.',
      ),
    ).toBeInTheDocument()
    // A family nothing raises yet says so.
    expect(screen.getAllByText('Noch keine Meldungen dieser Art.')).toHaveLength(2)
  })

  it('saves a family with If-Match on the version it read', async () => {
    const user = userEvent.setup()
    const backend = renderPreferences([
      {
        method: 'PUT',
        match: /\/me\/notification-preferences\/administration$/,
        handler: (req) => ({ ...PREFERENCES[1], ...(req.body as object), version: 1 }),
      },
    ])
    await user.click(await screen.findByRole('switch', { name: 'Push – Verwaltung' }))
    await waitFor(() => expect(backend.callsTo(/administration$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/administration$/, 'PUT')[0]
    expect(call.headers.get('If-Match')).toBe('0')
    expect(call.body).toEqual({ timing: 'immediate', inApp: true, push: false })
  })

  it('says when push is not set up on this installation', async () => {
    renderPreferences([])
    expect(await screen.findByText('Push ist auf dieser Installation noch nicht eingerichtet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Push auf diesem Gerät aktivieren' })).not.toBeInTheDocument()
  })

  it('says when this browser cannot push, once the platform can', async () => {
    renderPreferences([
      { method: 'GET', match: /\/me\/push\/config$/, handler: () => ({ available: true, vapidPublicKey: 'BKey' }) },
    ])
    // jsdom has no service worker or PushManager.
    expect(await screen.findByText('Dieser Browser unterstützt keine Push-Benachrichtigungen.')).toBeInTheDocument()
  })
})
