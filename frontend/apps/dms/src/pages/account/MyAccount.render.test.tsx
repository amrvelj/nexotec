// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Navigate, Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import type { ConnectedAccountRead } from '../../api/types'
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

const GRANT: ConnectedAccountRead = {
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
  const { queryClient } = renderWithProviders(
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
  return Object.assign(backend, { queryClient })
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
    expect(backend.callsTo(/\/me\/connected-accounts\/grant-1$/, 'DELETE')).toHaveLength(0) // asks once more
    await user.click(within(row).getByRole('button', { name: 'Ja, trennen' }))

    await waitFor(() => expect(backend.callsTo(/\/me\/connected-accounts\/grant-1$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/\/me\/connected-accounts\/grant-1$/, 'DELETE')[0].headers.get('If-Match')).toBe('1')
    expect(await screen.findByText('Getrennt')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Trennen' })).not.toBeInTheDocument()
  })

  it('says when and by whom a grant was ended', async () => {
    renderAccount('/account/connected-accounts', [
      {
        method: 'GET',
        match: /\/me\/connected-accounts$/,
        handler: () => ({
          items: [{ ...GRANT, status: 'revoked', revokedAt: '2026-10-20T08:00:00Z', revokedReason: 'manager', version: 2 }],
        }),
      },
    ])

    expect(await screen.findByText(/durch einen Manager/)).toBeInTheDocument()
  })

  it('a Disconnect refused as stale (409) shows the grant as it stands now', async () => {
    const user = userEvent.setup()
    let server = GRANT
    renderAccount('/account/connected-accounts', [
      { method: 'GET', match: /\/me\/connected-accounts$/, handler: () => ({ items: [server] }) },
      {
        method: 'DELETE',
        match: /\/me\/connected-accounts\/grant-1$/,
        handler: () => {
          // Revoked elsewhere meanwhile (the user's other device, a manager).
          server = { ...GRANT, status: 'revoked', revokedAt: '2026-10-20T08:00:00Z', revokedReason: 'manager', version: 2 }
          return { __status: 409, body: { error: { code: 'conflict', message: 'already revoked', details: {} } } }
        },
      },
    ])

    const row = (await screen.findByText('Microsoft 365')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Trennen' }))
    await user.click(within(row).getByRole('button', { name: 'Ja, trennen' }))

    expect(await screen.findByText('Getrennt')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Trennen' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/inzwischen geändert|bereits getrennt/)
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

  it('an untouched profile takes in a newer version; an edited one saves against the version it was loaded from', async () => {
    const user = userEvent.setup()
    let server = { ...ME }
    const backend = renderAccount('/account/profile', [
      { method: 'GET', match: /\/me$/, handler: () => server },
      { method: 'PATCH', match: /\/me$/, handler: () => ({ __status: 409, error: { code: 'conflict', message: 'stale', details: {} } }) },
    ])
    expect(await screen.findByDisplayValue('+41791234567')).toBeInTheDocument()

    // Someone else changes the phone; the untouched form shows it, Save stays off.
    server = { ...ME, phone: '+41790000000', version: 5 }
    await act(() => backend.queryClient.invalidateQueries({ queryKey: ['me'] }))
    expect(await screen.findByDisplayValue('+41790000000')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled()

    // An edit, then another change elsewhere: the save carries version 5 and
    // only the field edited — never the stale phone, never the newer version.
    const first = screen.getByLabelText(/^Vorname/)
    await user.clear(first)
    await user.type(first, 'Anja')
    server = { ...server, phone: '+41791111111', version: 6 }
    await act(() => backend.queryClient.invalidateQueries({ queryKey: ['me'] }))
    await user.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/me$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/\/me$/, 'PATCH')
    expect(call.headers.get('If-Match')).toBe('5')
    expect(call.body).toEqual({ firstName: 'Anja' })
    expect(await screen.findByText(/anderswo geändert/)).toBeInTheDocument()
  })

  it('sends a phone number typed with spaces as the API takes it', async () => {
    const user = userEvent.setup()
    const backend = renderAccount('/account/profile', [
      { method: 'GET', match: /\/me$/, handler: () => ME },
      { method: 'PATCH', match: /\/me$/, handler: (req) => ({ ...ME, ...(req.body as object), version: 5 }) },
    ])
    const dial = await screen.findByLabelText(/^Direktwahl/)
    await user.type(dial, '+41 44 123 45 00')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/me$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/me$/, 'PATCH')[0].body).toEqual({ directDial: '+41441234500' })
  })

  it('the signature block saves against the version it was loaded from', async () => {
    const user = userEvent.setup()
    let server = { ...ME, signatureBlock: 'Alte Zeile' }
    const backend = renderAccount('/account/signature', [
      { method: 'GET', match: /\/me$/, handler: () => server },
      { method: 'PATCH', match: /\/me$/, handler: () => ({ __status: 409, error: { code: 'conflict', message: 'stale', details: {} } }) },
    ])
    const block = await screen.findByLabelText(/^Unterschriftsblock/)
    await user.type(block, ' – neu')
    server = { ...server, signatureBlock: 'Von anderswo', version: 5 }
    await act(() => backend.queryClient.invalidateQueries({ queryKey: ['me'] }))
    await user.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/me$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/\/me$/, 'PATCH')
    expect(call.headers.get('If-Match')).toBe('4')
    expect(call.body).toEqual({ signatureBlock: 'Alte Zeile – neu' })

    // The 409 says what happened and offers the way out: the newer version replaces the edit.
    expect(await screen.findByText(/Ihr Konto wurde inzwischen anderswo geändert. Laden Sie/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Neuen Stand laden' }))
    expect(screen.getByLabelText(/^Unterschriftsblock/)).toHaveValue('Von anderswo')
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled()
    expect(screen.queryByText(/Ihr Konto wurde inzwischen anderswo geändert. Laden Sie/)).not.toBeInTheDocument()
  })
})
