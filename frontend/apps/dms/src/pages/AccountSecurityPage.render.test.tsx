// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { AccountSecurityPage } from './AccountSecurityPage'

// KAN-196 — My account › Security: the authenticator enrolment shows the
// secret once, the confirmation hands out the recovery codes once, and the
// sessions list ends another session.

const SESSIONS = {
  items: [
    { id: 's-1', deviceSummary: 'Chromium', ipAddress: '10.0.0.1', createdAt: '2026-10-17T08:00:00Z', lastSeenAt: '2026-10-17T09:00:00Z', absoluteExpiresAt: '2026-10-17T22:00:00Z', current: true },
    { id: 's-2', deviceSummary: 'Safari iPhone', ipAddress: '10.0.0.2', createdAt: '2026-10-17T07:00:00Z', lastSeenAt: '2026-10-17T07:30:00Z', absoluteExpiresAt: '2026-10-17T22:00:00Z', current: false },
  ],
}

describe('AccountSecurityPage (KAN-196)', () => {
  it('enrols an authenticator app and shows the recovery codes once', async () => {
    const user = userEvent.setup()
    let enrolled = false
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/auth\/mfa$/,
        handler: () => ({ totpEnrolled: enrolled, passkeys: [], recoveryCodesRemaining: enrolled ? 10 : 0, enforced: true, enrolmentRequired: !enrolled }),
      },
      { method: 'GET', match: /\/auth\/sessions$/, handler: () => SESSIONS },
      { method: 'POST', match: /\/auth\/mfa\/totp\/enrol$/, handler: () => ({ secret: 'JBSWY3DPEHPK3PXP', provisioningUri: 'otpauth://totp/Nexotec%20DMS:anna?secret=JBSWY3DPEHPK3PXP' }) },
      {
        method: 'POST',
        match: /\/auth\/mfa\/totp\/confirm$/,
        handler: () => {
          enrolled = true
          return { codes: ['aaaaa-bbbbb', 'ccccc-ddddd'] }
        },
      },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/account/security" element={<AccountSecurityPage />} />
      </Routes>,
      { route: '/account/security' },
    )

    expect(await screen.findByText('Nicht eingerichtet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Einrichten' }))
    expect(await screen.findByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument()

    await user.type(screen.getByLabelText(/^Code/), '123456')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))

    expect(await screen.findByText(/aaaaa-bbbbb/)).toBeInTheDocument()
    expect(backend.callsTo(/\/auth\/mfa\/totp\/confirm$/, 'POST')[0].body).toEqual({ code: '123456' })
    await waitFor(() => expect(screen.getByText(/bei jeder Anmeldung wird ein Code verlangt/)).toBeInTheDocument())
    expect(screen.getByText("10 Wiederherstellungscodes übrig.")).toBeInTheDocument()
  })

  it('lists the sessions and ends another one, never the current', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /\/auth\/mfa$/, handler: () => ({ totpEnrolled: true, passkeys: [], recoveryCodesRemaining: 9, enforced: false, enrolmentRequired: false }) },
      { method: 'GET', match: /\/auth\/sessions$/, handler: () => SESSIONS },
      { method: 'DELETE', match: /\/auth\/sessions\/s-2$/, handler: () => ({ __status: 204 }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/account/security" element={<AccountSecurityPage />} />
      </Routes>,
      { route: '/account/security' },
    )

    expect(await screen.findByText('Safari iPhone')).toBeInTheDocument()
    expect(screen.getByText('Diese Sitzung')).toBeInTheDocument()
    const endButtons = screen.getAllByRole('button', { name: 'Beenden' })
    expect(endButtons).toHaveLength(1)
    await user.click(endButtons[0])
    await waitFor(() => expect(backend.callsTo(/\/auth\/sessions\/s-2$/, 'DELETE')).toHaveLength(1))
  })
})
