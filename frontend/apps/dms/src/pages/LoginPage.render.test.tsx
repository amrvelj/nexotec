// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { LoginPage } from './LoginPage'

// KAN-196 — Nexotec's own sign-in screen (D-A-07): e-mail and password, the
// uniform refusal (FR-AU-16), and the second step when the account holds a
// factor.

const SESSION = {
  user: {
    id: 'user-1', dealershipId: 'd-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch', phone: null,
    role: 'sales', accessRoles: ['sales'], isDealerManager: true, employmentStatus: 'active', authIdentityId: 'local:user-1',
    status: 'active', version: 2, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  },
  activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
  memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
  entitlements: ['sales'],
  mfaEnrolmentRequired: false,
}

const SIGNED_OUT = { method: 'GET', match: /\/auth\/me$/, handler: () => status(401, { error: { code: 'unauthorized', message: 'x', details: null } }) }

function renderLogin() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<p>dashboard</p>} />
      <Route path="/account/security" element={<p>security screen</p>} />
    </Routes>,
    { route: '/login' },
  )
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('LoginPage (KAN-196)', () => {
  it('signs in with e-mail and password and lands on the dashboard', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      SIGNED_OUT,
      { method: 'POST', match: /\/auth\/login$/, handler: () => ({ mfaRequired: false, challengeId: null, factors: [], session: SESSION }) },
    ])
    renderLogin()

    await user.type(await screen.findByLabelText(/E-Mail-Adresse/), 'anna@example.ch')
    await user.type(screen.getByLabelText(/^Passwort/), 'correct-horse-battery-staple')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))

    expect(await screen.findByText('dashboard')).toBeInTheDocument()
    const [call] = backend.callsTo(/\/auth\/login$/, 'POST')
    expect(call.body).toEqual({ email: 'anna@example.ch', password: 'correct-horse-battery-staple' })
  })

  it('shows the one refusal for any 401, in the UI language', async () => {
    const user = userEvent.setup()
    await i18n.changeLanguage('fr')
    installFakeBackend([
      SIGNED_OUT,
      { method: 'POST', match: /\/auth\/login$/, handler: () => status(401, { error: { code: 'unauthorized', message: 'Invalid e-mail address or password.', details: null } }) },
    ])
    renderLogin()

    await user.type(await screen.findByLabelText(/Adresse e-mail/), 'nobody@example.ch')
    await user.type(screen.getByLabelText(/^Mot de passe/), 'whatever-it-is-12')
    await user.click(screen.getByRole('button', { name: 'Se connecter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Adresse e-mail ou mot de passe non valide.')
  })

  it('asks for the second factor and lands on enrolment when the session says so', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      SIGNED_OUT,
      { method: 'POST', match: /\/auth\/login$/, handler: () => ({ mfaRequired: true, challengeId: 'ch-1', factors: ['totp', 'recovery_code'], session: null }) },
      {
        method: 'POST',
        match: /\/auth\/mfa\/verify$/,
        handler: (req) =>
          (req.body as { code: string }).code === '123456'
            ? { mfaRequired: false, challengeId: null, factors: [], session: { ...SESSION, mfaEnrolmentRequired: true } }
            : status(401, { error: { code: 'unauthorized', message: 'The code is not valid.', details: null } }),
      },
    ])
    renderLogin()

    await user.type(await screen.findByLabelText(/E-Mail-Adresse/), 'anna@example.ch')
    await user.type(screen.getByLabelText(/^Passwort/), 'correct-horse-battery-staple')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))

    const codeInput = await screen.findByLabelText(/Code aus der Authenticator-App/)
    await user.type(codeInput, '000000')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Der Code ist ungültig.')

    await user.click(screen.getByRole('button', { name: 'Wiederherstellungscode verwenden' }))
    expect(screen.getByLabelText(/Wiederherstellungscode/)).toBeInTheDocument()
    await user.clear(screen.getByLabelText(/Wiederherstellungscode/))
    await user.type(screen.getByLabelText(/Wiederherstellungscode/), '123456')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))

    expect(await screen.findByText('security screen')).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/auth\/mfa\/verify$/, 'POST')).toHaveLength(2))
    expect(backend.callsTo(/\/auth\/mfa\/verify$/, 'POST')[1].body).toEqual({ challengeId: 'ch-1', code: '123456' })
  })
})
