// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import type { BrandRead } from '../../api/types'
import { CatalogueBrowsePage } from '../../pages/CatalogueBrowsePage'

// jsdom has no layout, so TanStack Virtual windows down to zero rows — mock
// it to "render every row" (the shim MappingGapsQueue.render.test.tsx uses).
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

// KAN-448 — brand create and edit on the Catalogue page (`?tab=brands`),
// for platform staff only (the brand write is `require_access_role()`):
// a create sends an Idempotency-Key, an edit changes the name only with
// If-Match from the row menu (ADR-061), and a refusal is said in the UI
// language.

const VW: BrandRead = { id: 'brand-vw', code: 'vw', displayName: 'Volkswagen', version: 2, createdAt: '2026-01-01T08:00:00Z', updatedAt: '2026-01-02T08:00:00Z' }

function me(accessRoles: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', dealershipId: 'dealership-1', firstName: 'Pia', lastName: 'Plattform', email: 'pia@example.ch', phone: null, role: 'admin', accessRoles, isDealerManager: false, employmentStatus: 'active', authIdentityId: 'local:u-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', createdBy: null, updatedBy: null },
      activeDealership: { id: 'dealership-1', legalName: 'Garage Nord AG', dealerGroupId: 'g-1' },
      memberships: [{ id: 'dealership-1', legalName: 'Garage Nord AG' }],
      entitlements: ['sales'],
    }),
  }
}

// The Browse tab's own reads, for the cases that land there.
const BROWSE: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/variants$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ facets: [] }) },
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
]

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('Catalogue › Marken (KAN-448)', () => {
  it('creates a brand with an Idempotency-Key and edits its name from the row menu with If-Match', async () => {
    const user = userEvent.setup()
    let brands: BrandRead[] = [VW]
    const backend = installFakeBackend([
      me(['platform_admin']),
      { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: brands, nextCursor: null }) },
      {
        method: 'POST',
        match: /\/vehicle-mdm\/brands$/,
        handler: (req) => {
          const created = { ...VW, id: 'brand-dkw', version: 1, ...(req.body as object) }
          brands = [...brands, created]
          return status(201, created)
        },
      },
      {
        method: 'PATCH',
        match: /\/vehicle-mdm\/brands\/brand-vw$/,
        handler: (req) => {
          brands = [{ ...VW, version: 3, ...(req.body as object) }, ...brands.slice(1)]
          return brands[0]
        },
      },
      ...BROWSE,
    ])
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?tab=brands' })

    expect(await screen.findByRole('tab', { name: 'Marken' })).toBeInTheDocument()
    expect(await screen.findByText('Volkswagen')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Marke erfassen' }))
    let dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByRole('textbox', { name: /^Code/ }), 'dkw')
    await user.type(dialog.getByRole('textbox', { name: /^Name/ }), 'DKW')
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/vehicle-mdm\/brands$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/vehicle-mdm\/brands$/, 'POST')
    expect(post.body).toEqual({ code: 'dkw', displayName: 'DKW' })
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await within(screen.getByRole('table')).findByText('DKW')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Zeilenaktionen' })[0])
    await user.click(await screen.findByText('Bearbeiten'))
    dialog = within(await screen.findByRole('dialog'))
    // The code is the brand's key: shown, never edited.
    expect(dialog.getByRole('textbox', { name: /^Code/ })).toHaveAttribute('readonly')
    const name = dialog.getByRole('textbox', { name: /^Name/ })
    await user.clear(name)
    await user.type(name, 'Volkswagen AG')
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/brands\/brand-vw$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/brands\/brand-vw$/, 'PATCH')
    expect(patch.body).toEqual({ displayName: 'Volkswagen AG' })
    expect(patch.headers.get('If-Match')).toBe('2')
    expect(await within(screen.getByRole('table')).findByText('Volkswagen AG')).toBeInTheDocument()
  })

  it('says a code already taken in Italian, never in the API\'s English', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    installFakeBackend([
      me(['platform_admin']),
      { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: [VW], nextCursor: null }) },
      {
        method: 'POST',
        match: /\/vehicle-mdm\/brands$/,
        handler: () => status(409, { error: { code: 'conflict', message: "A brand with code 'vw' already exists.", details: { reason: 'brand_code_taken', code: 'vw' } } }),
      },
      ...BROWSE,
    ])
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?tab=brands' })

    await user.click(await screen.findByRole('button', { name: i18n.t('brandsAdmin.create') }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByRole('textbox', { name: new RegExp(`^${i18n.t('brandsAdmin.fields.code')}`) }), 'vw')
    await user.type(dialog.getByRole('textbox', { name: new RegExp(`^${i18n.t('brandsAdmin.fields.displayName')}`) }), 'VW')
    await user.click(dialog.getByRole('button', { name: i18n.t('common.save') }))

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('apiRefusal.reasons.brand_code_taken'))
    expect(screen.queryByText(/already exists/)).toBeNull()
  })

  it('is no tab for anyone but platform staff, and their link opens Browse', async () => {
    installFakeBackend([me(['sales']), { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: [VW], nextCursor: null }) }, ...BROWSE])
    renderWithProviders(<CatalogueBrowsePage />, { route: '/catalogue?tab=brands' })

    expect(await screen.findByRole('tab', { name: 'Durchsuchen' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: 'Marken' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Marke erfassen' })).toBeNull()
  })
})
