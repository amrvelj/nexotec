// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-198 (D-A-04, D-A-10): a Betrieb without the `sales` entitlement — a
// service-only garage — never sees a stock, sales or valuations entry. The
// API's 404 is the boundary; the nav merely stops offering what it cannot
// use. Master data and administration entries are never hidden.

const USER = {
  id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
  phone: null, role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false,
  employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

function renderShell(entitlements: string[]) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1920 })
  installFakeBackend([
    {
      method: 'GET',
      match: /\/me\/preferences\/ui$/,
      handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage: 'de', density: 'default' } }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: USER,
        activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
        memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
        entitlements,
      }),
    },
  ])
  return renderWithProviders(
    <DmsShell>
      <div />
    </DmsShell>,
  )
}

afterEach(async () => {
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell navigation follows the module entitlements (KAN-198)', () => {
  const t = (key: string) => i18n.getFixedT('de')(key)

  it('a workshop without the sales entitlement sees no sales, stock or valuations entry', async () => {
    renderShell(['aftersales'])
    const nav = await screen.findByRole('navigation', { name: t('shell.sidebar.mainNavigation') })
    expect(await within(nav).findByText(t('shell.nav.customers'))).toBeInTheDocument()
    expect(within(nav).getByText(t('shell.nav.aftersales'))).toBeInTheDocument()
    for (const hidden of ['shell.nav.sales', 'shell.nav.inventory', 'shell.nav.valuations']) {
      expect(within(nav).queryByText(t(hidden))).not.toBeInTheDocument()
    }
    // never role- or entitlement-hidden: master data and administration
    expect(within(nav).getByText(t('shell.nav.vehicles'))).toBeInTheDocument()
    expect(within(nav).getByText(t('shell.nav.referenceData'))).toBeInTheDocument()
  })

  it('a fully entitled Betrieb sees every module entry', async () => {
    renderShell(ALL_ENTITLEMENTS)
    const nav = await screen.findByRole('navigation', { name: t('shell.sidebar.mainNavigation') })
    for (const shown of ['shell.nav.sales', 'shell.nav.inventory', 'shell.nav.valuations', 'shell.nav.aftersales']) {
      expect(await within(nav).findByText(t(shown))).toBeInTheDocument()
    }
  })
})
