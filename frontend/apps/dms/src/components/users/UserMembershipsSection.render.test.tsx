// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import type { MembershipRead } from '../../api/types'
import { UserMembershipsSection } from './UserMembershipsSection'

// KAN-407 — a user's access to sister Betriebe on the Users screen, on the
// KAN-199 endpoints: listed as the API lists it, granted with an
// Idempotency-Key, ended and flagged with If-Match on the user's version; a
// Betrieb the manager does not manage (404) is said as such, and an empty
// listing never claims the user has no other access.

const HOME = 'dealership-1'
const PATH = /\/dealerships\/dealership-1\/users\/u-1\/memberships/
const SOUTH = { id: 'dealership-2', legalName: 'Garage Süd AG', dealerGroupId: 'g-1' }
const WEST = { id: 'dealership-3', legalName: 'Garage West AG', dealerGroupId: 'g-1' }

function membership(overrides: Partial<MembershipRead> = {}): MembershipRead {
  return {
    dealershipId: SOUTH.id,
    dealershipLabel: SOUTH.legalName,
    isDealerManager: false,
    grantedAt: '2026-10-01T08:00:00Z',
    grantedBy: 'u-9',
    validTo: null,
    active: true,
    ...overrides,
  }
}

function me(accessRoles: string[] = ['sales']) {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-9', dealershipId: HOME, firstName: 'Boss', lastName: 'Meier', email: 'boss@example.ch', phone: null, role: 'admin', accessRoles, isDealerManager: true, employmentStatus: 'active', authIdentityId: 'local:u-9', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', createdBy: null, updatedBy: null },
      activeDealership: { id: HOME, legalName: 'Garage Nord AG', dealerGroupId: 'g-1' },
      memberships: [{ id: HOME, legalName: 'Garage Nord AG' }, { id: SOUTH.id, legalName: SOUTH.legalName }, { id: WEST.id, legalName: WEST.legalName }],
      entitlements: ['sales'],
    }),
  }
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('UserMembershipsSection (KAN-407)', () => {
  it('lists the access, grants one with an Idempotency-Key and ends one with If-Match on the user version', async () => {
    const user = userEvent.setup()
    let items: MembershipRead[] = [membership()]
    let version = 3
    const onUserVersion = vi.fn()
    const backend = installFakeBackend([
      me(),
      { method: 'GET', match: /\/memberships$/, handler: () => ({ items, userVersion: version }) },
      {
        method: 'POST',
        match: /\/memberships$/,
        handler: (req) => {
          const body = req.body as { dealershipId: string; isDealerManager: boolean }
          const row = membership({ dealershipId: body.dealershipId, dealershipLabel: WEST.legalName, isDealerManager: body.isDealerManager })
          items = [...items, row]
          return status(201, row)
        },
      },
      {
        method: 'DELETE',
        match: /\/memberships\/dealership-2$/,
        handler: () => {
          items = items.map((m) => (m.dealershipId === SOUTH.id ? { ...m, active: false, validTo: '2026-10-30T09:00:00Z' } : m))
          version += 1
          return status(204, null)
        },
      },
    ])
    renderWithProviders(<UserMembershipsSection dealershipId={HOME} userId="u-1" onUserVersion={onUserVersion} />)

    const table = await screen.findByRole('table', { name: 'Zugänge zu weiteren Betrieben' })
    expect(within(table).getByText('Garage Süd AG')).toBeInTheDocument()
    expect(within(table).getByText('Aktiv')).toBeInTheDocument()

    // Grantable: the Betriebe the signed-in user works in, minus this one and those already held.
    await user.click(screen.getByRole('textbox', { name: 'Zugang gewähren zu' }))
    expect(screen.queryByRole('option', { name: 'Garage Nord AG' })).toBeNull()
    expect(screen.queryByRole('option', { name: 'Garage Süd AG' })).toBeNull()
    await user.click(await screen.findByRole('option', { name: 'Garage West AG' }))
    await user.click(screen.getByLabelText('Als Manager'))
    await user.click(screen.getByRole('button', { name: 'Zugang gewähren' }))

    await waitFor(() => expect(backend.callsTo(/\/memberships$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/memberships$/, 'POST')
    expect(post.pathname).toMatch(PATH)
    expect(post.body).toEqual({ dealershipId: WEST.id, isDealerManager: true })
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await within(table).findByText('Garage West AG')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Zugang zu Garage Süd AG beenden' }))
    await waitFor(() => expect(backend.callsTo(/\/memberships\/dealership-2$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/\/memberships\/dealership-2$/, 'DELETE')[0].headers.get('If-Match')).toBe('3')
    expect(await screen.findByText(/^Beendet am /)).toBeInTheDocument()
    // The form learns the user's new version, so its own save does not meet a stale one.
    await waitFor(() => expect(onUserVersion).toHaveBeenLastCalledWith(4))
  })

  it('sets the manager flag in a sister Betrieb with If-Match', async () => {
    const user = userEvent.setup()
    let items: MembershipRead[] = [membership()]
    const backend = installFakeBackend([
      me(),
      { method: 'GET', match: /\/memberships$/, handler: () => ({ items, userVersion: 5 }) },
      {
        method: 'PUT',
        match: /\/memberships\/dealership-2\/manager-flag$/,
        handler: (req) => {
          items = [membership({ isDealerManager: (req.body as { isDealerManager: boolean }).isDealerManager })]
          return items[0]
        },
      },
    ])
    renderWithProviders(<UserMembershipsSection dealershipId={HOME} userId="u-1" onUserVersion={() => {}} />)

    await user.click(await screen.findByRole('switch', { name: 'Manager in Garage Süd AG' }))
    await waitFor(() => expect(backend.callsTo(/\/manager-flag$/, 'PUT')).toHaveLength(1))
    const [put] = backend.callsTo(/\/manager-flag$/, 'PUT')
    expect(put.body).toEqual({ isDealerManager: true })
    expect(put.headers.get('If-Match')).toBe('5')
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Manager in Garage Süd AG' })).toBeChecked())
  })

  it('says a Betrieb the manager does not manage, in French, and an empty listing is not «no access»', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([
      me(),
      { method: 'GET', match: /\/memberships$/, handler: () => ({ items: [], userVersion: 1 }) },
      {
        method: 'POST',
        match: /\/memberships$/,
        handler: () => status(404, { error: { code: 'not_found', message: 'Dealership dealership-3 was not found.', details: null } }),
      },
    ])
    renderWithProviders(<UserMembershipsSection dealershipId={HOME} userId="u-1" onUserVersion={() => {}} />)

    expect(await screen.findByText(i18n.t('usersAdmin.memberships.noneYouManage'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('usersAdmin.memberships.none'))).toBeNull()

    await user.click(screen.getByRole('textbox', { name: i18n.t('usersAdmin.memberships.grantTarget') }))
    await user.click(await screen.findByRole('option', { name: 'Garage West AG' }))
    await user.click(screen.getByRole('button', { name: i18n.t('usersAdmin.memberships.grant') }))

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('usersAdmin.memberships.notYours'))
    expect(screen.queryByText(/was not found/)).toBeNull()
  })

  it('tells platform staff plainly that the user has no other access, and offers the Betrieb\'s dealer group to grant', async () => {
    const user = userEvent.setup()
    const staff = me(['platform_admin'])
    // Platform staff hold no sister membership (KAN-199): their session lists their home only.
    const staffSession = { ...staff, handler: () => ({ ...(staff.handler as () => Record<string, unknown>)(), memberships: [{ id: HOME, legalName: 'Garage Nord AG' }] }) }
    const backend = installFakeBackend([
      staffSession,
      { method: 'GET', match: /\/memberships$/, handler: () => ({ items: [], userVersion: 1 }) },
      { method: 'GET', match: /^\/dealerships\/dealership-1$/, handler: () => ({ id: HOME, legalName: 'Garage Nord AG', dealerGroupId: 'g-1' }) },
      {
        method: 'GET',
        match: /^\/dealerships$/,
        handler: (req) => ({
          items: req.params.get('dealer_group_id') === 'g-1' ? [{ id: HOME, legalName: 'Garage Nord AG' }, SOUTH, WEST] : [],
          nextCursor: null,
        }),
      },
      { method: 'POST', match: /\/memberships$/, handler: () => status(201, membership()) },
    ])
    renderWithProviders(<UserMembershipsSection dealershipId={HOME} userId="u-1" onUserVersion={() => {}} />)

    expect(await screen.findByText('Kein Zugang zu einem weiteren Betrieb.')).toBeInTheDocument()
    await user.click(await screen.findByRole('textbox', { name: 'Zugang gewähren zu' }))
    expect(screen.queryByRole('option', { name: 'Garage Nord AG' })).toBeNull()
    await user.click(await screen.findByRole('option', { name: 'Garage Süd AG' }))
    await user.click(screen.getByRole('button', { name: 'Zugang gewähren' }))
    await waitFor(() => expect(backend.callsTo(/\/memberships$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/memberships$/, 'POST')[0].body).toEqual({ dealershipId: SOUTH.id, isDealerManager: false })
  })
})
