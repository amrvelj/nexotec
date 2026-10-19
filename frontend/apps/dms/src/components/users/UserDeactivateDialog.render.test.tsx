// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import type { UserRead } from '../../api/types'
import { UserDeactivateDialog } from './UserDeactivateDialog'

// KAN-200 — the reassignment step (FR-A-37): every open group needs a
// decision before the leaver can be deactivated; the request carries the
// reason, one transfer per group (a colleague or null), If-Match and an
// Idempotency-Key.

function person(id: string, firstName: string, lastName: string, overrides: Partial<UserRead> = {}): UserRead {
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
        onDeactivated={(r) => {
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

  it('says a last-manager refusal and keeps the dialog open', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /\/users\/u-1\/ownership$/, handler: () => ({ groups: [], modules: ['customer', 'sales'] }) },
      {
        method: 'POST',
        match: /\/users\/u-1\/deactivate$/,
        handler: () => status(400, { error: { code: 'bad_request', message: 'This dealership must always have at least one active manager — cannot remove or deactivate its last one.', details: null } }),
      },
    ])
    renderWithProviders(<UserDeactivateDialog opened onClose={() => {}} dealershipId="dealership-1" user={LEAVER} colleagues={[LEAVER]} onDeactivated={() => {}} />)
    const dialog = within(await screen.findByRole('dialog'))
    expect(await dialog.findByText('Nichts offen in keinem der 2 Module.')).toBeInTheDocument()
    await user.type(dialog.getByLabelText(/^Grund/), 'weg')
    await user.click(dialog.getByRole('button', { name: 'Deaktivieren' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at least one active manager')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
