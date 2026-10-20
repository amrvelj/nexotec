// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Navigate, Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { AccountConnectedAccountsPage } from './AccountConnectedAccountsPage'
import { AccountLayout } from './AccountLayout'
import { AccountProfilePage } from './AccountProfilePage'
import { AccountSignaturePage } from './AccountSignaturePage'

// KAN-201 — My account: the tabs, the profile (own name, contact, the two
// languages kept apart), connected accounts (an empty state until the
// Outlook integration links one; revoke) and the signature (text and image).

const ME = {
  id: 'user-1',
  dealershipId: 'dealership-1',
  email: 'anna@garage.ch',
  firstName: 'Anna',
  lastName: 'Muster',
  phone: '+41791234567',
  directDial: null,
  correspondenceLanguage: null,
  defaultCorrespondenceLanguage: 'de',
  signatureBlock: null,
  hasSignatureImage: false,
  version: 4,
}

const GRANT = {
  id: 'grant-1',
  provider: 'microsoft_365',
  externalAccountLabel: 'anna@garage.ch',
  scopes: ['Mail.Send', 'Calendars.ReadWrite'],
  status: 'active',
  linkedAt: '2026-10-01T08:00:00Z',
  lastUsedAt: null,
  revokedAt: null,
  revokedReason: null,
  version: 1,
}

function renderAccount(route: string, routes: FakeRoute[]) {
  const backend = installFakeBackend(routes)
  renderWithProviders(
    <Routes>
      <Route path="/account" element={<AccountLayout />}>
        <Route index element={<Navigate to="/account/profile" replace />} />
        <Route path="profile" element={<AccountProfilePage />} />
        <Route path="security" element={<div>security tab</div>} />
        <Route path="connected-accounts" element={<AccountConnectedAccountsPage />} />
        <Route path="signature" element={<AccountSignaturePage />} />
      </Route>
    </Routes>,
    { route },
  )
  return backend
}

describe('My account (KAN-201)', () => {
  it('opens on the profile and offers the four tabs', async () => {
    renderAccount('/account', [{ method: 'GET', match: /\/me$/, handler: () => ME }])

    expect(await screen.findByRole('heading', { name: 'Mein Konto' })).toBeInTheDocument()
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Profil', 'Sicherheit', 'Verbundene Konten', 'Unterschrift'])
    expect(screen.getByRole('tab', { name: 'Profil' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByDisplayValue('Muster')).toBeInTheDocument()
  })

  it('saves only what changed in the profile, with If-Match, and keeps the e-mail read-only', async () => {
    const user = userEvent.setup()
    const backend = renderAccount('/account/profile', [
      { method: 'GET', match: /\/me$/, handler: () => ME },
      { method: 'PATCH', match: /\/me$/, handler: (req) => ({ ...ME, ...(req.body as object), version: 5 }) },
    ])

    const first = await screen.findByLabelText(/^Vorname/)
    expect(screen.getByLabelText(/^E-Mail-Adresse/)).toBeDisabled()
    await user.clear(first)
    await user.type(first, 'Anja')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/me$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/\/me$/, 'PATCH')
    expect(call.body).toEqual({ firstName: 'Anja' })
    expect(call.headers.get('If-Match')).toBe('4')
    expect(await screen.findByText('Gespeichert.')).toBeInTheDocument()
  })

  it('keeps the UI language and the language of my documents as two controls', async () => {
    renderAccount('/account/profile', [{ method: 'GET', match: /\/me$/, handler: () => ME }])

    expect(await screen.findByRole('textbox', { name: /^Sprache der Anwendung/ })).toHaveValue('Deutsch')
    const documents = screen.getByRole('textbox', { name: /^Sprache meiner Dokumente/ })
    expect(documents).toHaveValue('Wie der Betrieb (Deutsch)')
    expect(screen.getByText('Benachrichtigungen')).toBeInTheDocument()
  })

  it('explains connected accounts while none is linked', async () => {
    renderAccount('/account/connected-accounts', [
      { method: 'GET', match: /\/me\/connected-accounts$/, handler: () => ({ items: [] }) },
    ])

    expect(await screen.findByText('Noch keine verbundenen Konten')).toBeInTheDocument()
    expect(screen.getByText(/nie zur Anmeldung/)).toBeInTheDocument()
  })

  it('lists a connected account and revokes it with If-Match', async () => {
    const user = userEvent.setup()
    let revoked = false
    const backend = renderAccount('/account/connected-accounts', [
      {
        method: 'GET',
        match: /\/me\/connected-accounts$/,
        handler: () => ({ items: [revoked ? { ...GRANT, status: 'revoked', version: 2 } : GRANT] }),
      },
      {
        method: 'DELETE',
        match: /\/me\/connected-accounts\/grant-1$/,
        handler: () => {
          revoked = true
          return { ...GRANT, status: 'revoked', version: 2 }
        },
      },
    ])

    const row = (await screen.findByText('Microsoft 365')).closest('tr') as HTMLElement
    expect(within(row).getByText('Mail.Send, Calendars.ReadWrite')).toBeInTheDocument()
    expect(within(row).getByText('Aktiv')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Trennen' }))

    await waitFor(() => expect(backend.callsTo(/\/me\/connected-accounts\/grant-1$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/\/me\/connected-accounts\/grant-1$/, 'DELETE')[0].headers.get('If-Match')).toBe('1')
    expect(await screen.findByText('Getrennt')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Trennen' })).not.toBeInTheDocument()
  })

  it('saves the signature block and uploads the signature image as the raw body', async () => {
    const user = userEvent.setup()
    let me = ME
    const backend = renderAccount('/account/signature', [
      { method: 'GET', match: /\/me$/, handler: () => me },
      {
        method: 'PATCH',
        match: /\/me$/,
        handler: (req) => (me = { ...me, ...(req.body as object), version: me.version + 1 }),
      },
      {
        method: 'PUT',
        match: /\/me\/signature$/,
        handler: () => (me = { ...me, hasSignatureImage: true, version: me.version + 1 }),
      },
      { method: 'GET', match: /\/me\/signature$/, handler: () => ({ __status: 404 }) },
    ])

    const block = await screen.findByLabelText(/^Unterschriftsblock/)
    await user.type(block, 'Verkaufsberater')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/me$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/me$/, 'PATCH')[0].body).toEqual({ signatureBlock: 'Verkaufsberater' })

    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'signature.png', { type: 'image/png' })
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, png)
    await user.click(screen.getByRole('button', { name: 'Hochladen' }))

    await waitFor(() => expect(backend.callsTo(/\/me\/signature$/, 'PUT')).toHaveLength(1))
    const [put] = backend.callsTo(/\/me\/signature$/, 'PUT')
    expect(put.headers.get('If-Match')).toBe('5')
    expect(put.headers.get('Content-Type')).toBe('image/png')
  })

  it('shows only the Security tab while a second factor is still owed', async () => {
    renderAccount('/account/security', [
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@garage.ch', role: 'sales', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'active', authIdentityId: 'local:user-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
          activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
          memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
          entitlements: [],
          mfaEnrolmentRequired: true,
        }),
      },
    ])

    expect(await screen.findByText('security tab')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Sicherheit']))
  })
})
