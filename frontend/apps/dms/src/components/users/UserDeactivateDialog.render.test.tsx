// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import type { UserAdminRead } from '../../api/types'
import { UserDeactivateDialog } from './UserDeactivateDialog'

// KAN-200 — the reassignment step (FR-A-37): every open group needs a
// decision before the leaver can be deactivated; the request carries the
// reason, one transfer per group (a colleague or null), If-Match and an
// Idempotency-Key.

function person(id: string, firstName: string, lastName: string, overrides: Partial<UserAdminRead> = {}): UserAdminRead {
  return {
    id,
    dealershipId: 'dealership-1',
    firstName,
    lastName,
    email: `${firstName.toLowerCase()}@example.ch`,
    phone: null,
    role: 'sales',
    accessRoles: ['sales'],
    isDealerManager: false,
    employmentStatus: 'active',
    authIdentityId: `local:${id}`,
    status: 'active',
    version: 4,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    updatedBy: null,
    ...overrides,
  }
}

const LEAVER = person('u-1', 'Lea', 'Ver')
const HEIR = person('u-2', 'Hans', 'Erbe')

describe('UserDeactivateDialog (KAN-200)', () => {
  it('lists what the leaver owns, needs a decision per group, and sends the hand-over', async () => {
    const user = userEvent.setup()
    let result: unknown = null
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/users\/u-1\/ownership$/,
        handler: () => ({
          groups: [
            { module: 'customer', group: 'customers', count: 12 },
            { module: 'sales', group: 'offers', count: 3 },
          ],
          modules: ['aftersales', 'customer', 'inventory', 'parts', 'sales'],
        }),
      },
      {
        method: 'POST',
        match: /\/users\/u-1\/deactivate$/,
        handler: () => ({
          user: { ...LEAVER, status: 'deactivated', version: 5 },
          transfers: [
            { module: 'customer', group: 'customers', toUserId: 'u-2', count: 12 },
            { module: 'sales', group: 'offers', toUserId: null, count: 3 },
          ],
        }),
      },
    ])
    renderWithProviders(
      <UserDeactivateDialog
        opened
        onClose={() => {}}
        dealershipId="dealership-1"
        user={LEAVER}
        colleagues={[LEAVER, HEIR]}
        onDone={(r) => {
          result = r
        }}
      />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    expect(await dialog.findByText('Betreute Kunden')).toBeInTheDocument()
    expect(dialog.getByText('Offene Offerten')).toBeInTheDocument()
    expect(dialog.getByText('12')).toBeInTheDocument()
    const confirm = dialog.getByRole('button', { name: 'Deaktivieren' })
    expect(confirm).toBeDisabled()

    await user.type(dialog.getByLabelText(/^Grund/), 'ausgetreten per 30.9.')
    expect(confirm).toBeDisabled() // the groups are still undecided

    await user.click(dialog.getByRole('textbox', { name: 'Übergeben: Betreute Kunden' }))
    await user.click(await screen.findByRole('option', { name: 'Hans Erbe' }))
    await user.click(dialog.getByRole('textbox', { name: 'Übergeben: Offene Offerten' }))
    await user.click(await screen.findByRole('option', { name: 'Nicht zuweisen (Warteschlange des Moduls)' }))
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    await waitFor(() => expect(result).not.toBeNull())
    const [post] = backend.callsTo(/\/users\/u-1\/deactivate$/, 'POST')
    expect(post.headers.get('If-Match')).toBe('4')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(post.body).toEqual({
      reason: 'ausgetreten per 30.9.',
      employmentStatus: 'terminated',
      transfers: [
        { module: 'customer', group: 'customers', toUserId: 'u-2' },
        { module: 'sales', group: 'offers', toUserId: null },
      ],
    })
  })

  it('hands open records over without deactivating — the retry after a module failed', async () => {
    const user = userEvent.setup()
    let result: unknown = null
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /\/users\/u-1\/ownership$/,
        handler: () => ({ groups: [{ module: 'sales', group: 'offers', count: 3 }], modules: ['customer', 'sales'] }),
      },
      {
        method: 'POST',
        match: /\/users\/u-1\/ownership\/transfers$/,
        handler: () => ({ transfers: [{ module: 'sales', group: 'offers', toUserId: 'u-2', count: 3 }] }),
      },
    ])
    renderWithProviders(
      <UserDeactivateDialog
        opened
        onClose={() => {}}
        dealershipId="dealership-1"
        user={{ ...LEAVER, status: 'deactivated' }}
        colleagues={[LEAVER, HEIR]}
        mode="transfer"
        onDone={(r) => {
          result = r
        }}
      />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('Offene Vorgänge von Lea Ver übergeben')).toBeInTheDocument()
    expect(dialog.queryByLabelText(/Anstellung ist beendet/)).not.toBeInTheDocument()
    const confirm = dialog.getByRole('button', { name: 'Übergeben' })
    expect(confirm).toBeDisabled()
    await user.click(await dialog.findByRole('textbox', { name: 'Übergeben: Offene Offerten' }))
    await user.click(await screen.findByRole('option', { name: 'Hans Erbe' }))
    expect(confirm).toBeEnabled() // no reason needed for a plain hand-over
    await user.click(confirm)

    await waitFor(() => expect(result).not.toBeNull())
    const [post] = backend.callsTo(/\/users\/u-1\/ownership\/transfers$/, 'POST')
    expect(post.headers.get('If-Match')).toBeNull()
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(post.body).toEqual({ transfers: [{ module: 'sales', group: 'offers', toUserId: 'u-2' }], reason: null })
    expect(backend.callsTo(/\/deactivate$/, 'POST')).toHaveLength(0)
  })

  it('offers only active colleagues as receivers (FR-A-37)', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'GET',
        match: /\/users\/u-1\/ownership$/,
        handler: () => ({ groups: [{ module: 'sales', group: 'offers', count: 1 }], modules: ['sales'] }),
      },
    ])
    const invited = person('u-3', 'Ida', 'Invited', { status: 'invited' })
    const suspended = person('u-4', 'Sven', 'Suspended', { status: 'suspended' })
    renderWithProviders(
      <UserDeactivateDialog opened onClose={() => {}} dealershipId="dealership-1" user={LEAVER} colleagues={[LEAVER, HEIR, invited, suspended]} onDone={() => {}} />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(await dialog.findByRole('textbox', { name: 'Übergeben: Offene Offerten' }))
    const options = (await screen.findAllByRole('option')).map((o) => o.textContent)
    expect(options).toEqual(['Nicht zuweisen (Warteschlange des Moduls)', 'Hans Erbe'])
  })

  it('lists the problems a 422 names, so the manager knows which group to change', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'GET',
        match: /\/users\/u-1\/ownership$/,
        handler: () => ({ groups: [{ module: 'sales', group: 'offers', count: 2 }], modules: ['sales'] }),
      },
      {
        method: 'POST',
        match: /\/users\/u-1\/deactivate$/,
        handler: () =>
          status(422, {
            error: {
              code: 'unprocessable_entity',
              message: 'The reassignment step is incomplete.',
              details: {
                problems: [
                  { module: 'sales', group: 'offers', reason: 'colleague_cannot_act_there', count: 1, message: 'sales/offers: 1 of these records belong to another business the colleague cannot act in — …' },
                  { module: 'customer', group: 'customers', reason: 'undecided', message: 'customer/customers: needs a decision: a colleague, or null to leave it unassigned' },
                ],
                openGroups: [],
              },
            },
          }),
      },
    ])
    renderWithProviders(<UserDeactivateDialog opened onClose={() => {}} dealershipId="dealership-1" user={LEAVER} colleagues={[LEAVER, HEIR]} onDone={() => {}} />)
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/^Grund/), 'weg')
    await user.click(await dialog.findByRole('textbox', { name: 'Übergeben: Offene Offerten' }))
    await user.click(await screen.findByRole('option', { name: 'Hans Erbe' }))
    await user.click(dialog.getByRole('button', { name: 'Deaktivieren' }))
    // Each problem in the UI language, from its reason code — never the API's English.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Die Übergabe ist noch nicht vollständig:')
    expect(alert).toHaveTextContent('Offene Offerten: 1 davon gehören zu einem anderen Betrieb')
    expect(alert).toHaveTextContent('Betreute Kunden: braucht eine Entscheidung')
    expect(alert).not.toHaveTextContent('belong to another business')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('says a last-manager refusal and keeps the dialog open', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /\/users\/u-1\/ownership$/, handler: () => ({ groups: [], modules: ['customer', 'sales'] }) },
      {
        method: 'POST',
        match: /\/users\/u-1\/deactivate$/,
        handler: () => status(400, { error: { code: 'bad_request', message: 'This dealership must always have at least one active manager — cannot remove or deactivate its last one.', details: { reason: 'last_manager' } } }),
      },
    ])
    renderWithProviders(<UserDeactivateDialog opened onClose={() => {}} dealershipId="dealership-1" user={LEAVER} colleagues={[LEAVER]} onDone={() => {}} />)
    const dialog = within(await screen.findByRole('dialog'))
    expect(await dialog.findByText('In keinem der 2 Module ist etwas offen.')).toBeInTheDocument()
    await user.type(dialog.getByLabelText(/^Grund/), 'weg')
    await user.click(dialog.getByRole('button', { name: 'Deaktivieren' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('mindestens einen aktiven Manager')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
