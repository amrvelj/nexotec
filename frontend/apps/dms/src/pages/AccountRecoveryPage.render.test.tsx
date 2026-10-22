// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { AccountRecoveryPage } from './AccountRecoveryPage'

// KAN-202 — Administration › Account recovery (FR-AU-25…27): staff find an
// account by its exact address or by Betrieb, read its state (lock, factor,
// sessions) and act from the row menu — every action through the reason
// dialog, never on a click; entries the API would refuse are disabled with
// the reason; anyone but staff is told the page is staff's (the API's 403).

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

function account(overrides: Record<string, unknown>) {
  return {
    id: 'u-1',
    dealershipId: 'd-1',
    dealershipName: 'Garage Nord AG',
    email: 'anna@garage-nord.ch',
    firstName: 'Anna',
    lastName: 'Keller',
    status: 'active',
    isDealerManager: true,
    isPlatformStaff: false,
    hasPassword: true,
    lockedUntil: '2026-10-22T09:15:00Z',
    failedLoginAttempts: 5,
    mfaEnrolled: true,
    openSessions: 2,
    version: 4,
    ...overrides,
  }
}

const BEAT = account({ id: 'u-2', firstName: 'Beat', lastName: 'Frei', email: 'beat@garage-nord.ch', isDealerManager: false, lockedUntil: null, failedLoginAttempts: 0, mfaEnrolled: false, openSessions: 0 })

function routes(items = [account({}), BEAT]) {
  return [
    { method: 'GET', match: /\/dealerships$/, handler: () => ({ items: [{ id: 'd-1', legalName: 'Garage Nord AG', tradeName: null }], nextCursor: null }) },
    { method: 'GET', match: /\/platform\/users$/, handler: () => ({ items, nextCursor: null }) },
  ]
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, index: number) {
  await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[index])
  return within(await screen.findByRole('menu'))
}

describe('AccountRecoveryPage (KAN-202)', () => {
  it('shows each account with its Betrieb, lock, factor and sessions', async () => {
    const backend = installFakeBackend(routes())
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?dealershipId=d-1' })

    expect(await screen.findByText('Anna Keller')).toBeInTheDocument()
    expect(screen.getByText('Beat Frei')).toBeInTheDocument()
    expect(screen.getAllByText('Garage Nord AG').length).toBeGreaterThan(0)
    expect(screen.getByText(/^Gesperrt bis/)).toBeInTheDocument()
    expect(screen.getByText('Eingerichtet')).toBeInTheDocument()
    expect(backend.callsTo(/\/platform\/users$/, 'GET')[0].params.get('dealershipId')).toBe('d-1')
  })

  it('disables what the API would refuse and says why', async () => {
    const user = userEvent.setup()
    installFakeBackend(routes())
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?dealershipId=d-1' })
    await screen.findByText('Beat Frei')

    // Rows in the server's order: Anna Keller, then Beat Frei.
    const annas = await openMenu(user, 0) // a manager, locked
    expect(annas.getByText('Bereits Manager des Betriebs.')).toBeInTheDocument()
    expect(annas.getByText('Sperre aufheben').closest('button')).not.toBeDisabled()
    await user.keyboard('{Escape}')

    const beats = await openMenu(user, 1) // not locked, not a manager
    expect(beats.getByText('Das Konto ist nicht gesperrt.')).toBeInTheDocument()
    expect(beats.getByText('Zum Manager machen').closest('button')).not.toBeDisabled()
  })

  it('clears a lockout only with a stated reason, and says it was done', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes(),
      {
        method: 'POST',
        match: /\/platform\/users\/u-1\/recovery\/clear-lockout$/,
        handler: () => ({ action: 'clear-lockout', userId: 'u-1', dealershipId: 'd-1', userVersion: 4, authEventId: 'e-1', occurredAt: '2026-10-22T09:00:00Z' }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?email=anna%40garage-nord.ch' })
    await screen.findByText('Anna Keller')

    const menu = await openMenu(user, 0)
    await user.click(menu.getByText('Sperre aufheben'))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('Sperre von Anna Keller aufheben')).toBeInTheDocument()
    // KAN-216 is not built: the dialog says the user is not notified, never that they are.
    expect(dialog.getByText(/Eine Benachrichtigung des Benutzers ist noch nicht gebaut/)).toBeInTheDocument()
    const submit = dialog.getByRole('button', { name: 'Sperre aufheben' })
    expect(submit).toBeDisabled()
    expect(backend.callsTo(/\/recovery\//, 'POST')).toHaveLength(0)

    await user.type(dialog.getByLabelText(/Begründung/), 'Anruf des Inhabers, Ticket 4711')
    await user.click(submit)
    await waitFor(() => expect(backend.callsTo(/\/recovery\/clear-lockout$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/recovery\/clear-lockout$/, 'POST')
    expect(call.body).toEqual({ reason: 'Anruf des Inhabers, Ticket 4711' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await screen.findByText('Die Sperre von Anna Keller wurde aufgehoben.')).toBeInTheDocument()
  })

  it('makes a user the manager through the Betrieb', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes(),
      {
        method: 'POST',
        match: /\/platform\/dealerships\/d-1\/recovery\/grant-manager$/,
        handler: () => ({ action: 'grant-manager', userId: 'u-2', dealershipId: 'd-1', userVersion: 5, authEventId: 'e-2', occurredAt: '2026-10-22T09:00:00Z' }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?dealershipId=d-1' })
    await screen.findByText('Beat Frei')

    const menu = await openMenu(user, 1)
    await user.click(menu.getByText('Zum Manager machen'))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('Beat Frei zum Manager von Garage Nord AG machen')).toBeInTheDocument()
    await user.type(dialog.getByLabelText(/Begründung/), 'Der bisherige Manager hat den Betrieb verlassen')
    await user.click(dialog.getByRole('button', { name: 'Zum Manager machen' }))
    await waitFor(() => expect(backend.callsTo(/\/grant-manager$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/grant-manager$/, 'POST')
    expect(call.body).toEqual({ userId: 'u-2', reason: 'Der bisherige Manager hat den Betrieb verlassen' })
    expect(call.headers.get('If-Match')).toBe('4') // the version the search read
  })

  it("says the API's refusal in the UI language", async () => {
    const user = userEvent.setup()
    installFakeBackend([
      ...routes([BEAT]),
      {
        method: 'POST',
        match: /\/recovery\/revoke-sessions$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'This account is not locked out.', details: { rule: 'not_locked' } } }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?email=beat%40garage-nord.ch' })
    await screen.findByText('Beat Frei')

    const menu = await openMenu(user, 0)
    await user.click(menu.getByText('Alle Sitzungen beenden'))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/Begründung/), 'Gerät verloren')
    await user.click(dialog.getByRole('button', { name: 'Sitzungen beenden' }))
    expect(await dialog.findByText('Das Konto ist nicht gesperrt — es gibt nichts aufzuheben.')).toBeInTheDocument()
  })

  it('moves the sign-in address with a reason and the version the search read', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes([BEAT]),
      {
        method: 'POST',
        match: /\/platform\/users\/u-2\/recovery\/change-email$/,
        handler: () => ({ action: 'change-email', userId: 'u-2', dealershipId: 'd-1', userVersion: 5, authEventId: 'e-3', occurredAt: '2026-10-22T09:00:00Z' }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?email=beat%40garage-nord.ch' })
    await screen.findByText('Beat Frei')

    const menu = await openMenu(user, 0)
    await user.click(menu.getByText('E-Mail-Adresse ändern'))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText(/beat@garage-nord.ch wird über die Änderung informiert/)).toBeInTheDocument()
    const submit = dialog.getByRole('button', { name: 'Adresse ändern' })
    await user.type(dialog.getByLabelText(/Begründung/), 'Neue Domain des Betriebs')
    expect(submit).toBeDisabled() // no address yet
    await user.type(dialog.getByLabelText(/Neue E-Mail-Adresse/), 'beat@neu.ch')
    await user.click(submit)
    await waitFor(() => expect(backend.callsTo(/\/change-email$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/change-email$/, 'POST')
    expect(call.body).toEqual({ email: 'beat@neu.ch', reason: 'Neue Domain des Betriebs' })
    expect(call.headers.get('If-Match')).toBe('4')
    expect(await screen.findByText('Die E-Mail-Adresse von Beat Frei wurde geändert.')).toBeInTheDocument()
  })

  it('asks the server for the sort in the URL and loads the next page by cursor', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /\/dealerships$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'GET',
        match: /\/platform\/users$/,
        handler: (req) => (req.params.get('cursor') ? { items: [BEAT], nextCursor: null } : { items: [account({})], nextCursor: 'c-2' }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?dealershipId=d-1&sort=lastName:desc' })
    await screen.findByText('Anna Keller')

    // The grid asks for the next page when it reaches the end: Beat arrives
    // from the second request, with the first page's cursor.
    expect(await screen.findByText('Beat Frei')).toBeInTheDocument()
    const calls = backend.callsTo(/\/platform\/users$/, 'GET')
    expect(calls.map((c) => c.params.get('cursor'))).toEqual([null, 'c-2'])
    expect(calls.every((c) => c.params.get('sort') === 'lastName:desc')).toBe(true) // sorted by the server
  })

  it('sends the surname-then-first-name order when the URL names none', async () => {
    const backend = installFakeBackend(routes([BEAT]))
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?dealershipId=d-1' })
    await screen.findByText('Beat Frei')
    expect(backend.callsTo(/\/platform\/users$/, 'GET')[0].params.get('sort')).toBe('lastName:asc,firstName:asc')
  })

  it('says a validation refusal without a rule in the UI language, never the API\'s English', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      ...routes([BEAT]),
      {
        method: 'POST',
        match: /\/recovery\/revoke-sessions$/,
        handler: () => status(422, { error: { code: 'validation_error', message: 'Request validation failed.', details: null } }),
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?email=beat%40garage-nord.ch' })
    await screen.findByText('Beat Frei')

    const menu = await openMenu(user, 0)
    await user.click(menu.getByText('Alle Sitzungen beenden'))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/Begründung/), 'Gerät verloren')
    await user.click(dialog.getByRole('button', { name: 'Sitzungen beenden' }))
    expect(await dialog.findByText('Die Angaben sind nicht gültig — prüfen Sie die Felder.')).toBeInTheDocument()
    expect(dialog.queryByText(/Request validation failed/)).not.toBeInTheDocument()
  })

  it('refuses an address that is not one before asking the server, in the UI language', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend(routes([BEAT]))
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery?email=beat%40garage-nord.ch' })
    await screen.findByText('Beat Frei')

    const menu = await openMenu(user, 0)
    await user.click(menu.getByText('E-Mail-Adresse ändern'))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/Neue E-Mail-Adresse/), 'beat@neu')
    await user.type(dialog.getByLabelText(/Begründung/), 'Tippfehler-Probe')
    await user.click(dialog.getByRole('button', { name: 'Adresse ändern' }))
    expect(await dialog.findByText('Diese E-Mail-Adresse ist nicht gültig.')).toBeInTheDocument()
    expect(backend.callsTo(/\/change-email$/, 'POST')).toHaveLength(0)
  })

  it('offers every Betrieb, page after page', async () => {
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/dealerships$/,
        handler: (req) =>
          req.params.get('cursor')
            ? { items: [{ id: 'd-2', legalName: 'Garage Süd AG', tradeName: null }], nextCursor: null }
            : { items: [{ id: 'd-1', legalName: 'Garage Nord AG', tradeName: null }], nextCursor: 'c-2' },
      },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery' })
    await waitFor(() => expect(backend.callsTo(/\/dealerships$/, 'GET')).toHaveLength(2))
    expect(backend.callsTo(/\/dealerships$/, 'GET')[1].params.get('cursor')).toBe('c-2')
  })

  it("tells anyone but staff that the page is staff's", async () => {
    installFakeBackend([
      { method: 'GET', match: /\/dealerships$/, handler: () => status(403, { error: { code: 'forbidden', message: 'no', details: null } }) },
      { method: 'GET', match: /\/platform\/users$/, handler: () => status(403, { error: { code: 'forbidden', message: 'no', details: null } }) },
    ])
    renderWithProviders(<AccountRecoveryPage />, { route: '/administration/account-recovery' })

    expect(await screen.findByText('Nicht verfügbar')).toBeInTheDocument()
    expect(screen.queryByRole('grid')).not.toBeInTheDocument()
  })
})
