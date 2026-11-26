// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '../../../i18n'
import App from '../../../App'
import { testTheme } from '../../../test/testTheme'
import { ALL_GATED_CAPABILITIES, installFakeBackend } from '../../../test/fakeBackend'
import { order, summary } from '../test/serviceOrderFixtures'

// KAN-810 criterion 7: through the real route table, the workshop orders are Aftersales' — a
// Betrieb with Aftersales and without Sales reaches the list and the order page, and a Betrieb
// without Aftersales is sent to the dashboard (D-A-04, KAN-198); the API's 404 is the boundary.

function signedIn(entitlements: string[]) {
  installFakeBackend([
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: {
          id: 'user-1', dealershipId: 'dealership-1', firstName: 'Sandra', lastName: 'Keller', email: 'sk@example.ch',
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
    { method: 'GET', match: /^\/service-orders$/, handler: () => ({ items: [summary()], nextCursor: null, total: 1, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/service-orders\/order-1$/, handler: () => order() },
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

describe('the workshop order routes (KAN-810)', () => {
  it('a Betrieb with Aftersales and without Sales opens /aftersales', async () => {
    signedIn(['aftersales'])
    renderApp('/aftersales')
    expect(await screen.findByRole('heading', { level: 2, name: i18n.t('serviceOrders.title') })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/aftersales')
  })

  it('and the order page /aftersales/orders/:id', async () => {
    signedIn(['aftersales'])
    renderApp('/aftersales/orders/order-1')
    expect(await screen.findByText('Mercedes-Benz GLC · W1KBMRB80MN6DTN3X · Reto Wyss')).toBeInTheDocument()
  })

  it('a Betrieb without Aftersales is sent to the dashboard, even with Sales', async () => {
    signedIn(['sales'])
    renderApp('/aftersales/orders/order-1')
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(screen.queryByText('Mercedes-Benz GLC · W1KBMRB80MN6DTN3X · Reto Wyss')).not.toBeInTheDocument()
  })
})
