// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { render } from '@testing-library/react'
import { installFakeBackend } from '../test/fakeBackend'
import { AuthProvider } from '../auth/AuthContext'
import { RequireEntitlement } from './RequireEntitlement'

// KAN-198: the layout route sends a Betrieb without the module to the
// dashboard instead of rendering a screen the API answers with 404.

const USER = {
  id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
  phone: null, role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false,
  employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

function renderAt(path: string, entitlements: string[]) {
  installFakeBackend([
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
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<div>dashboard</div>} />
          <Route element={<RequireEntitlement module="sales" />}>
            <Route path="/stock" element={<div>stock screen</div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('RequireEntitlement (KAN-198)', () => {
  it('sends a Betrieb without the module to the dashboard', async () => {
    renderAt('/stock', ['aftersales'])
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
    expect(screen.queryByText('stock screen')).not.toBeInTheDocument()
  })

  it('renders the screen for an entitled Betrieb', async () => {
    renderAt('/stock', ['sales'])
    expect(await screen.findByText('stock screen')).toBeInTheDocument()
  })
})
