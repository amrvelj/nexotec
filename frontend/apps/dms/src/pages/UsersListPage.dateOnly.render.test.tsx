// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { UsersListPage } from './UsersListPage'

// KAN-200 — `validFrom` / `validTo` are date-only values (a calendar day the
// access opens or closes); formatDateOnly keeps the day west of Greenwich,
// where formatDate would render the day before (KAN-333's rule for a field
// whose name does not say which it is: the screen's own test under this zone).

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

const ROW = {
  id: 'u-1',
  dealershipId: 'dealership-1',
  firstName: 'Anna',
  lastName: 'Keller',
  email: 'anna@example.ch',
  phone: null,
  role: 'sales',
  accessRoles: ['sales'],
  isDealerManager: false,
  employmentStatus: 'active',
  authIdentityId: 'local:u-1',
  status: 'active',
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  createdBy: null,
  updatedBy: null,
  validFrom: '2026-11-01',
  validTo: '2027-03-31',
  lastLoginAt: null,
  invitedAt: null,
  deactivatedAt: null,
  hasSignatureImage: false,
  invitationExpiresAt: null,
  primaryLocationId: null,
  additionalLocationIds: [],
}

describe('UsersListPage — validity dates keep their day west of Greenwich (KAN-200)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders validFrom and validTo as the recorded calendar day', async () => {
    installFakeBackend([
      { method: 'GET', match: /\/dealerships\/dealership-1\/users$/, handler: () => ({ items: [ROW], nextCursor: null }) },
      { method: 'GET', match: /\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [] }) },
    ])
    // The two columns are hidden by default (ADR-060: available, not visible); the
    // grid preference the Columns panel writes shows them.
    window.localStorage.setItem(
      'dms.preferences.grid.administration.users.list',
      JSON.stringify({ schemaVersion: 1, sort: [], columnLayout: { order: ['name', 'validFrom', 'validTo'], hidden: [], widths: {}, pinnedLeft: [] } }),
    )
    renderWithProviders(<UsersListPage />, { route: '/administration/users' })
    const grid = within(await screen.findByRole('table'))
    await grid.findByText('Anna Keller')
    expect(grid.getByText('01.11.2026')).toBeInTheDocument()
    expect(grid.getByText('31.03.2027')).toBeInTheDocument()
  })
})
