// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../i18n'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, status } from '../test/fakeBackend'
import { InvitationAcceptPage } from './InvitationAcceptPage'
import { PasswordResetConfirmPage } from './PasswordResetConfirmPage'
import { PasswordResetRequestPage } from './PasswordResetRequestPage'

// KAN-196 — the three unauthenticated screens the e-mail links land on, and
// the enrolment redirect of ProtectedRoute.

const SIGNED_OUT = { method: 'GET', match: /\/auth\/me$/, handler: () => status(401, { error: { code: 'unauthorized', message: 'x', details: null } }) }
const DEAD_LINK = { error: { code: 'bad_request', message: 'This link is no longer valid — request a new one.', details: { rule: 'link' } } }

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('reset and invitation screens (KAN-196)', () => {
  it('the reset request says the same thing whatever the address', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([SIGNED_OUT, { method: 'POST', match: /\/auth\/password-reset\/request$/, handler: () => status(202) }])
    renderWithProviders(
      <Routes>
        <Route path="/password-reset" element={<PasswordResetRequestPage />} />
      </Routes>,
      { route: '/password-reset' },
    )
    await user.type(await screen.findByLabelText(/E-Mail-Adresse/), 'nobody@example.ch')
    await user.click(screen.getByRole('button', { name: 'Link senden' }))
    expect(await screen.findByText(/Wenn zu dieser Adresse ein Konto besteht/)).toBeInTheDocument()
    expect(backend.callsTo(/\/auth\/password-reset\/request$/, 'POST')[0].body).toEqual({ email: 'nobody@example.ch' })
  })

  it('a dead reset link is said as such, not as a reused password', async () => {
    const user = userEvent.setup()
    installFakeBackend([SIGNED_OUT, { method: 'POST', match: /\/auth\/password-reset\/confirm$/, handler: () => status(400, DEAD_LINK) }])
    renderWithProviders(
      <Routes>
        <Route path="/password-reset/confirm" element={<PasswordResetConfirmPage />} />
      </Routes>,
      { route: '/password-reset/confirm?token=expired' },
    )
    await user.type(await screen.findByLabelText(/Neues Passwort/), 'correct-horse-battery-staple')
    await user.click(screen.getByRole('button', { name: 'Passwort setzen' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Dieser Link ist nicht mehr gültig. Fordern Sie einen neuen an.')
  })

  it('a reset link without a token is refused before any request', async () => {
    installFakeBackend([SIGNED_OUT])
    renderWithProviders(
      <Routes>
        <Route path="/password-reset/confirm" element={<PasswordResetConfirmPage />} />
      </Routes>,
      { route: '/password-reset/confirm' },
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Dieser Link ist nicht mehr gültig')
  })

  it('an invitation to a user with a second factor hands over to the sign-in', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      SIGNED_OUT,
      { method: 'POST', match: /\/auth\/invitations\/accept$/, handler: () => ({ mfaRequired: true, challengeId: 'ch-1', factors: ['totp', 'recovery_code'], session: null }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/invitations/accept" element={<InvitationAcceptPage />} />
      </Routes>,
      { route: '/invitations/accept?token=t-1' },
    )
    await user.type(await screen.findByLabelText(/^Passwort/), 'correct-horse-battery-staple')
    await user.click(screen.getByRole('button', { name: 'Konto aktivieren' }))
    expect(await screen.findByText('Ihr Passwort ist gesetzt. Melden Sie sich jetzt mit Ihrem zweiten Faktor an.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Zur Anmeldung' })).toHaveAttribute('href', '/login')
  })

  it('a session that still owes a second factor is sent to the security screen first', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: { id: 'u-1', dealershipId: 'd-1', firstName: 'A', lastName: 'B', email: 'a@example.ch', phone: null, role: 'sales', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'active', authIdentityId: 'local:u-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
          activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
          memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
          entitlements: ALL_ENTITLEMENTS,
          mfaEnrolmentRequired: true,
        }),
      },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/" element={<ProtectedRoute><p>dashboard</p></ProtectedRoute>} />
        <Route path="/account/security" element={<ProtectedRoute><p>security screen</p></ProtectedRoute>} />
      </Routes>,
      { route: '/' },
    )
    expect(await screen.findByText('security screen')).toBeInTheDocument()
    expect(screen.queryByText('dashboard')).not.toBeInTheDocument()
  })
})
