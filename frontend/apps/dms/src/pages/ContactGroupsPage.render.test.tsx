// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRequest, type FakeRoute } from '../test/fakeBackend'
import { ContactGroupsPage } from './ContactGroupsPage'

// KAN-466 — Dealer Administration › Kontaktgruppen: the platform's list, one
// switch per group for the Betrieb; any role reads, the manager switches; a
// group Nexotec withdrew cannot be switched on.

const contactGroup = (valueCode: string, labelDe: string, overrides: Record<string, unknown> = {}) => ({
  valueCode,
  labelDe,
  labelFr: `${labelDe} (fr)`,
  labelIt: `${labelDe} (it)`,
  labelEn: `${labelDe} (en)`,
  sortOrder: 0,
  active: true,
  enabled: false,
  ...overrides,
})

const GROUPS = [
  contactGroup('private', 'Privat', { enabled: true }),
  contactGroup('fleet', 'Flotte'),
  contactGroup('insurer', 'Versicherung', { active: false }),
]

function install(extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships\/dealership-1\/contact-groups$/, handler: () => ({ items: GROUPS }) },
  ])
}

afterEach(() => {
  cleanup()
})

const row = (name: string) =>
  within(screen.getByRole('table', { name: i18n.t('contactGroups.title') })).getByRole('row', {
    name: (n) => n.startsWith(name),
  })

describe('ContactGroupsPage (KAN-466)', () => {
  it('lists every group with its state and says what switching off does', async () => {
    install()
    renderWithProviders(<ContactGroupsPage />, { route: '/administration/contact-groups' })
    await screen.findByRole('table', { name: i18n.t('contactGroups.title') })
    expect(within(row('Privat')).getByText(i18n.t('contactGroups.status.on'))).toBeInTheDocument()
    expect(within(row('Flotte')).getByText(i18n.t('contactGroups.status.off'))).toBeInTheDocument()
    expect(within(row('Versicherung')).getByText(i18n.t('contactGroups.status.inactive'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('contactGroups.offNotice'))).toBeInTheDocument()
    // A withdrawn group cannot be switched on.
    expect(within(row('Versicherung')).getByRole('switch')).toBeDisabled()
  })

  it('the manager switches a group on through the PUT', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'PUT',
        match: /^\/dealerships\/dealership-1\/contact-groups\/fleet$/,
        handler: (req: FakeRequest) => contactGroup('fleet', 'Flotte', { enabled: (req.body as { enabled: boolean }).enabled }),
      },
    ])
    renderWithProviders(<ContactGroupsPage />, { route: '/administration/contact-groups' })
    await screen.findByRole('table', { name: i18n.t('contactGroups.title') })
    await user.click(within(row('Flotte')).getByRole('switch', { name: i18n.t('contactGroups.switchLabel', { group: 'Flotte' }) }))
    await waitFor(() => expect(within(row('Flotte')).getByText(i18n.t('contactGroups.status.on'))).toBeInTheDocument())
    const puts = backend.callsTo(/\/contact-groups\/fleet$/, 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0].body).toEqual({ enabled: true })
  })

  it('says a refusal in the UI language', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'PUT',
        match: /\/contact-groups\/fleet$/,
        handler: () =>
          status(422, { error: { code: 'unprocessable_entity', message: 'English', details: { reason: 'contact_group_inactive' } } }),
      },
    ])
    renderWithProviders(<ContactGroupsPage />, { route: '/administration/contact-groups' })
    await screen.findByRole('table', { name: i18n.t('contactGroups.title') })
    await user.click(within(row('Flotte')).getByRole('switch'))
    expect(await screen.findByText(i18n.t('apiRefusal.reasons.contact_group_inactive'))).toBeInTheDocument()
  })

  it('a role without the write reads the list and switches nothing', async () => {
    install([
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Test', lastName: 'Sales', email: 's@example.ch', accessRoles: ['sales'], isDealerManager: false, status: 'active', version: 1 },
          activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' },
          memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' }],
          entitlements: ['sales'],
          writableCapabilities: [],
        }),
      },
    ])
    renderWithProviders(<ContactGroupsPage />, { route: '/administration/contact-groups' })
    await screen.findByRole('table', { name: i18n.t('contactGroups.title') })
    expect(screen.getByText(i18n.t('contactGroups.readOnly'))).toBeInTheDocument()
    for (const name of ['Privat', 'Flotte']) expect(within(row(name)).getByRole('switch')).toBeDisabled()
  })
})
