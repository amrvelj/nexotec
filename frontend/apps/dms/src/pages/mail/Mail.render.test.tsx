// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { AccountMailboxPage } from '../account/AccountMailboxPage'
import { MailAdministrationPage } from '../MailAdministrationPage'
import { MailLinkCallbackPage } from '../MailLinkCallbackPage'
import { mailNavigation } from './mailLink'

// KAN-215 — customer mail through the dealer's own mailbox: Administration ›
// Kundenmails (the Betrieb's shared mailbox), My account › Mein Postfach (the
// user's own, and which address customers see right now — D1 A) and the
// provider's callback page.

const PROVIDERS = [
  { code: 'microsoft_365_mail', configured: true },
  { code: 'google_workspace_mail', configured: false },
]

const mailbox = (overrides: Record<string, unknown> = {}) => ({
  id: 'mb-1',
  kind: 'business_mailbox',
  provider: 'microsoft_365_mail',
  mailboxAddress: 'info@garage-muster.ch',
  status: 'active',
  errorCode: null,
  connectedAt: '2026-11-18T10:00:00Z',
  version: 1,
  ...overrides,
})

const READ_ONLY_ME: FakeRoute = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Test', lastName: 'Finance', email: 'f@example.ch', accessRoles: ['finance'], isDealerManager: false, status: 'active', version: 1 },
    activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' },
    memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' }],
    entitlements: ['sales'],
    writableCapabilities: [],
  }),
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Administration › Kundenmails (KAN-215)', () => {
  it('a manager connects the shared mailbox: the provider sign-in opens, unconfigured providers are disabled', async () => {
    const user = userEvent.setup()
    const toProvider = vi.spyOn(mailNavigation, 'toProvider').mockImplementation(() => {})
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/dealerships\/dealership-1\/mail\/business-mailbox$/, handler: () => ({ mailbox: null, providers: PROVIDERS }) },
      {
        method: 'POST',
        match: /^\/dealerships\/dealership-1\/mail\/business-mailbox\/links$/,
        handler: () => status(201, { authorizeUrl: 'https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?state=s' }),
      },
    ])
    renderWithProviders(<MailAdministrationPage />, { route: '/administration/mail' })

    expect(await screen.findByText(i18n.t('mail.business.none'))).toBeInTheDocument()
    // What the consent allows and how IT narrows it, in the dealer's words.
    expect(screen.getByText(i18n.t('mail.business.granted.microsoft_365_mail'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('mail.business.restrict.microsoft_365_mail'))).toBeInTheDocument()
    expect(i18n.t('mail.business.restrict.microsoft_365_mail')).toContain('RBAC for Applications')

    await user.type(screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('mail.business.addressLabel')) }), 'info@garage-muster.ch')
    await user.click(screen.getByRole('button', { name: i18n.t('mail.business.connect') }))

    await waitFor(() => expect(toProvider).toHaveBeenCalledWith(expect.stringContaining('login.microsoftonline.com')))
    const [call] = backend.callsTo(/business-mailbox\/links$/, 'POST')
    expect(call.body).toEqual({ provider: 'microsoft_365_mail', mailboxAddress: 'info@garage-muster.ch' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('shows the connected mailbox and disconnects it with If-Match after a confirmation', async () => {
    const user = userEvent.setup()
    let current: unknown = mailbox()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/dealerships\/dealership-1\/mail\/business-mailbox$/, handler: () => ({ mailbox: current, providers: PROVIDERS }) },
      {
        method: 'DELETE',
        match: /^\/dealerships\/dealership-1\/mail\/business-mailbox\/mb-1$/,
        handler: () => {
          current = null
          return status(204)
        },
      },
    ])
    renderWithProviders(<MailAdministrationPage />, { route: '/administration/mail' })
    expect(
      await screen.findByText(i18n.t('mail.business.connectedAs', { address: 'info@garage-muster.ch', provider: 'Microsoft 365' })),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('mail.business.disconnect') }))
    expect(screen.getByText(i18n.t('mail.business.disconnectNote'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('mail.business.confirmDisconnect') }))

    await screen.findByText(i18n.t('mail.business.none'))
    expect(backend.callsTo(/business-mailbox\/mb-1$/, 'DELETE')[0].headers.get('If-Match')).toBe('1')
  })

  it('a reader sees the mailbox and is offered no write', async () => {
    installFakeBackend([
      READ_ONLY_ME,
      { method: 'GET', match: /^\/dealerships\/dealership-1\/mail\/business-mailbox$/, handler: () => ({ mailbox: mailbox(), providers: PROVIDERS }) },
    ])
    renderWithProviders(<MailAdministrationPage />, { route: '/administration/mail' })
    expect(await screen.findByText(i18n.t('mail.business.readOnly'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('mail.business.disconnect') })).not.toBeInTheDocument()
  })
})

describe('My account › Mein Postfach (KAN-215)', () => {
  it('says why nothing can be sent and links both connection screens', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/integrations\/mail\/own-mailbox$/,
        handler: () => ({ mailbox: null, sender: null, senderRefusal: 'no_mailbox_connected', providers: PROVIDERS }),
      },
    ])
    renderWithProviders(<AccountMailboxPage />, { route: '/account/mailbox' })
    expect(await screen.findByText(i18n.t('mail.refusals.no_mailbox_connected'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('mail.links.businessScreen') })).toHaveAttribute('href', '/administration/mail')
    expect(screen.getByRole('button', { name: i18n.t('mail.own.connect', { provider: 'Google Workspace' }) })).toBeDisabled()
  })

  it('an own mailbox in error is said as such, never as «no mailbox connected»', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/integrations\/mail\/own-mailbox$/,
        handler: () => ({
          mailbox: mailbox({ kind: 'user_mailbox', mailboxAddress: 'anna@garage-muster.ch', status: 'error' }),
          sender: null,
          senderRefusal: 'own_mailbox_in_error',
          providers: PROVIDERS,
        }),
      },
    ])
    renderWithProviders(<AccountMailboxPage />, { route: '/account/mailbox' })
    expect(await screen.findByText(i18n.t('mail.refusals.own_mailbox_in_error'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('mail.refusals.no_mailbox_connected'))).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('mail.status.error'))).toBeInTheDocument()
  })

  it('names the address customers see and starts the own link', async () => {
    const user = userEvent.setup()
    const toProvider = vi.spyOn(mailNavigation, 'toProvider').mockImplementation(() => {})
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/integrations\/mail\/own-mailbox$/,
        handler: () => ({
          mailbox: null,
          sender: { kind: 'business_mailbox', address: 'info@garage-muster.ch' },
          senderRefusal: null,
          providers: PROVIDERS,
        }),
      },
      { method: 'POST', match: /^\/integrations\/mail\/own-mailbox\/links$/, handler: () => status(201, { authorizeUrl: 'https://login.example/authorize' }) },
    ])
    renderWithProviders(<AccountMailboxPage />, { route: '/account/mailbox' })
    expect(
      await screen.findByText(i18n.t('mail.own.sender.business_mailbox', { address: 'info@garage-muster.ch' })),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('mail.own.connect', { provider: 'Microsoft 365' }) }))
    await waitFor(() => expect(toProvider).toHaveBeenCalledWith('https://login.example/authorize'))
  })
})

describe('The provider callback (KAN-215)', () => {
  const routes = (
    <Routes>
      <Route path="/mail-link/callback" element={<MailLinkCallbackPage />} />
      <Route path="/account/mailbox" element={<p>own mailbox screen</p>} />
      <Route path="/administration/mail" element={<p>business mailbox screen</p>} />
    </Routes>
  )

  it('posts the query once and goes on to Microsoft’s consent screen', async () => {
    const toProvider = vi.spyOn(mailNavigation, 'toProvider').mockImplementation(() => {})
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /^\/integrations\/mail\/link-callback$/,
        handler: () => ({ kind: 'business_mailbox', status: 'consent_required', redirectUrl: 'https://login.microsoftonline.com/t/v2.0/adminconsent', mailboxId: null }),
      },
    ])
    renderWithProviders(routes, { route: '/mail-link/callback?state=abc&code=xyz', strictMode: true })
    await waitFor(() => expect(toProvider).toHaveBeenCalledWith('https://login.microsoftonline.com/t/v2.0/adminconsent'))
    expect(backend.callsTo(/link-callback$/, 'POST')).toHaveLength(1)
    expect(backend.callsTo(/link-callback$/, 'POST')[0].body).toEqual({ state: 'abc', code: 'xyz' })
  })

  it('returns to the screen the link started from', async () => {
    installFakeBackend([
      {
        method: 'POST',
        match: /^\/integrations\/mail\/link-callback$/,
        handler: () => ({ kind: 'user_mailbox', status: 'connected', redirectUrl: null, mailboxId: 'mb-2' }),
      },
    ])
    renderWithProviders(routes, { route: '/mail-link/callback?state=abc&code=xyz' })
    expect(await screen.findByText('own mailbox screen')).toBeInTheDocument()
  })

  it('says a refusal in the UI language with the way back', async () => {
    installFakeBackend([
      {
        method: 'POST',
        match: /^\/integrations\/mail\/link-callback$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'not_an_administrator' } } }),
      },
    ])
    renderWithProviders(routes, { route: '/mail-link/callback?state=abc&code=xyz' })
    expect(await screen.findByText(i18n.t('mail.refusals.not_an_administrator'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('mail.links.ownScreen') })).toHaveAttribute('href', '/account/mailbox')
  })
})
