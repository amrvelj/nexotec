// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '../../../i18n'
import App from '../../../App'
import { testTheme } from '../../../test/testTheme'
import { ALL_GATED_CAPABILITIES, installFakeBackend } from '../../../test/fakeBackend'

// KAN-809 (review): the planner is Aftersales' — through the real route table,
// a Betrieb with Aftersales and without Sales reaches /planner, and a Betrieb
// without Aftersales is sent to the dashboard (D-A-04, KAN-198).

function signedIn(entitlements: string[]) {
  installFakeBackend([
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: {
          id: 'user-1', dealershipId: 'dealership-1', firstName: 'Anna', lastName: 'Chef', email: 'anna@example.ch',
          phone: null, role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false,
          employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
          createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
        activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
        memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
        entitlements,
        writableCapabilities: ALL_GATED_CAPABILITIES,
      }),
    },
    { match: /\/locations$/, handler: () => ({ items: [] }) },
  ])
}

function renderApp(path: string) {
  window.history.replaceState(null, '', path)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MantineProvider theme={testTheme} env="test" withCssVariables={false} withGlobalClasses={false}>
        <App />
      </MantineProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => window.history.replaceState(null, '', '/'))

describe('the planner route (KAN-809)', () => {
  it('a Betrieb with Aftersales and without Sales opens /planner', async () => {
    signedIn(['aftersales'])
    renderApp('/planner')
    expect(await screen.findByRole('heading', { level: 2, name: i18n.t('planner.title') })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/planner')
  })

  it('a Betrieb without Aftersales is sent to the dashboard, even with Sales', async () => {
    signedIn(['sales'])
    renderApp('/planner')
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(screen.queryByRole('heading', { level: 2, name: i18n.t('planner.title') })).not.toBeInTheDocument()
  })
})
