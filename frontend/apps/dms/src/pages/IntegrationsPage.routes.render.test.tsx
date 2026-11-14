// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { AdministrationIntegrationsPage, IntegrationsRedirect, PlatformProvidersPage } from './IntegrationsPage'

// KAN-222 — the dealer's integrations are a section of Dealer Administration
// (`/administration/integrations`, A-RULE-0); platform staff's grid lives at
// `/platform/providers`; the old `/integrations` sends each to theirs.

function me(accessRoles: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: {
        id: 'u-1', dealershipId: 'dealership-1', firstName: 'Mia', lastName: 'Muster', email: 'mia@example.ch',
        phone: null, role: 'admin', accessRoles, isDealerManager: true, employmentStatus: 'active',
        authIdentityId: 'local:u-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z', createdBy: null, updatedBy: null,
      },
      activeDealership: { id: 'dealership-1', legalName: 'Garage Nord AG', dealerGroupId: 'g-1' },
      memberships: [{ id: 'dealership-1', legalName: 'Garage Nord AG' }],
      entitlements: ['sales'],
    }),
  }
}

const EMPTY_PAGE = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

function install(accessRoles: string[]) {
  return installFakeBackend([
    me(accessRoles),
    { match: /^\/integrations\/providers$/, handler: () => ({ items: [] }) },
    { match: /^\/integrations\/connections$/, handler: () => EMPTY_PAGE },
    { match: /^\/dealerships$/, handler: () => EMPTY_PAGE },
  ])
}

function routes() {
  return (
    <Routes>
      <Route path="/integrations" element={<IntegrationsRedirect />} />
      <Route path="/administration/integrations" element={<AdministrationIntegrationsPage />} />
      <Route path="/platform/providers" element={<PlatformProvidersPage />} />
    </Routes>
  )
}

describe('integration routes (KAN-222)', () => {
  it('sends a dealer from /integrations to Dealer Administration › Integrationen', async () => {
    const backend = install(['sales'])
    renderWithProviders(routes(), { route: '/integrations' })
    expect(await screen.findByRole('heading', { name: 'Integrationen' })).toBeInTheDocument()
    expect(backend.callsTo(/^\/integrations\/connections$/).length).toBeGreaterThan(0)
  })

  it('sends platform staff from /integrations to /platform/providers', async () => {
    install(['platform_admin'])
    renderWithProviders(routes(), { route: '/integrations' })
    expect(await screen.findByRole('heading', { name: 'Anbieter' })).toBeInTheDocument()
  })

  it('sends platform staff on the dealer screen to the providers grid', async () => {
    install(['platform_admin'])
    renderWithProviders(routes(), { route: '/administration/integrations' })
    expect(await screen.findByRole('heading', { name: 'Anbieter' })).toBeInTheDocument()
  })
})
