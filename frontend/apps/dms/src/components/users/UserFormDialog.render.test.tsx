// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import type { LocationRead, UserRead } from '../../api/types'
import { UserFormDialog } from './UserFormDialog'

// KAN-200 — the one user form: an invitation sends every field the PRD
// names with an Idempotency-Key; an edit PATCHes only what changed with
// If-Match, replaces the delegations through their own PUT, and shows the
// last-manager refusal as the API says it.

const DEALERSHIP = 'dealership-1'
const LOCATIONS: LocationRead[] = [
  { id: 'loc-1', name: 'Zürich Nord', addressLocality: 'Zürich' },
  { id: 'loc-2', name: 'Zürich Süd', addressLocality: 'Zürich' },
]

function existing(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: 'u-1',
    dealershipId: DEALERSHIP,
    firstName: 'Anna',
    lastName: 'Keller',
    email: 'anna@example.ch',
    phone: '+41791234567',
    role: 'sales',
    accessRoles: ['sales'],
    isDealerManager: true,
    employmentStatus: 'active',
    authIdentityId: 'local:u-1',
    status: 'active',
    version: 3,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    updatedBy: null,
    directDial: null,
    employeeNumber: null,
    department: null,
    lineManagerUserId: null,
    salesAdvisorCode: null,
    commissionRelevant: false,
    validFrom: null,
    validTo: null,
    invitedAt: null,
    lastLoginAt: null,
    deactivatedAt: null,
    deactivationReason: null,
    hasSignatureImage: false,
    invitationExpiresAt: null,
    primaryLocationId: 'loc-1',
    additionalLocationIds: [],
    ...overrides,
  }
}

const COLLEAGUE = existing({ id: 'u-9', firstName: 'Boss', lastName: 'Meier', email: 'boss@example.ch' })

describe('UserFormDialog (KAN-200)', () => {
  it('invites a colleague with the organisation, access and validity fields', async () => {
    const user = userEvent.setup()
    let saved: UserRead | null = null
    const backend = installFakeBackend([
      {
        method: 'POST',
        match: /\/dealerships\/dealership-1\/users$/,
        handler: (req) => status(201, existing({ id: 'u-new', ...(req.body as object), version: 1 })),
      },
      { method: 'PUT', match: /\/users\/u-new\/text-delegations$/, handler: () => ({ categories: ['document'], userVersion: 2 }) },
    ])
    renderWithProviders(
      <UserFormDialog
        opened
        onClose={() => {}}
        dealershipId={DEALERSHIP}
        user={null}
        locations={LOCATIONS}
        colleagues={[COLLEAGUE]}
        selfId="u-9"
        onSaved={(u) => {
          saved = u
        }}
      />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/^Vorname/), 'Dana')
    await user.type(dialog.getByLabelText(/^Nachname/), 'Vogel')
    await user.type(dialog.getByLabelText(/^E-Mail \(Anmeldung\)/), 'dana@example.ch')
    await user.type(dialog.getByLabelText(/^Mobile/), '+41791112233')
    await user.click(dialog.getByLabelText('Aftersales'))
    await user.click(dialog.getByLabelText('Dokumente'))
    await user.type(dialog.getByLabelText(/^Zugang bis/), '2027-03-31')
    await user.click(dialog.getByRole('button', { name: 'Einladung senden' }))

    await waitFor(() => expect(saved).not.toBeNull())
    const [post] = backend.callsTo(/\/dealerships\/dealership-1\/users$/, 'POST')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(post.body).toMatchObject({
      firstName: 'Dana',
      lastName: 'Vogel',
      email: 'dana@example.ch',
      phone: '+41791112233',
      role: 'sales',
      accessRoles: ['sales', 'aftersales'],
      isDealerManager: false,
      validTo: '2027-03-31',
      validFrom: null,
      commissionRelevant: false,
    })
    const [put] = backend.callsTo(/\/users\/u-new\/text-delegations$/, 'PUT')
    expect(put.body).toEqual({ categories: ['document'] })
    expect(put.headers.get('If-Match')).toBe('1')
    expect(saved!.version).toBe(2)
  })

  it('edits only what changed, with If-Match, and says a last-manager refusal as the API does', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /\/users\/u-1\/text-delegations$/, handler: () => ({ categories: [], userVersion: 3 }) },
      {
        method: 'PATCH',
        match: /\/users\/u-1$/,
        handler: () =>
          status(400, { error: { code: 'bad_request', message: 'This dealership must always have at least one active manager — cannot remove or deactivate its last one.', details: null } }),
      },
    ])
    renderWithProviders(
      <UserFormDialog opened onClose={() => {}} dealershipId={DEALERSHIP} user={existing()} locations={LOCATIONS} colleagues={[COLLEAGUE]} selfId="u-9" onSaved={() => {}} />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByLabelText(/^E-Mail \(Anmeldung\)/)).toBeDisabled() // an active user's address is recovery, not an edit
    await user.clear(dialog.getByLabelText(/^Abteilung/))
    await user.type(dialog.getByLabelText(/^Abteilung/), 'Verkauf')
    await user.click(dialog.getByRole('switch', { name: /Manager dieses Betriebs/ }))
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('at least one active manager')
    const [patch] = backend.callsTo(/\/users\/u-1$/, 'PATCH')
    expect(patch.headers.get('If-Match')).toBe('3')
    expect(patch.body).toEqual({ department: 'Verkauf', isDealerManager: false })
  })
})
