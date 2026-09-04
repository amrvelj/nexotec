// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomersListPage } from './CustomersListPage'
import { CustomerDetailPage } from './CustomerDetailPage'

// WP-6c exit criterion: "the URL reproduces search, filter, sort and tab
// when pasted into a fresh session" — and ADR-056 / FR-UI-06: column
// layout and density are deliberately NOT in the URL. Both halves are
// asserted here so nobody "fixes" the second one later.

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

describe('CustomersListPage — grid state is the URL (ADR-056)', () => {
  it('a pasted URL reproduces search, sort and filter on a fresh mount', async () => {
    const backend = installFakeBackend([
      {
        match: /^\/customers$/,
        handler: () => customerPage([customer({ id: 'c1', lastName: 'Aebi' }), customer({ id: 'c2', lastName: 'Zünd' })]),
      },
    ])

    const predicate = { id: 'p1', fieldId: 'canton', type: 'select', condition: 'is', value: 'BE' }
    renderWithProviders(
      <>
        <LocationProbe />
        <CustomersListPage />
      </>,
      {
        route: `/customers?q=Muster&sort=lastName:asc&filters=${encodeURIComponent(JSON.stringify([predicate]))}`,
      },
    )

    // Search box is populated straight from the URL.
    expect(await screen.findByDisplayValue('Muster')).toBeInTheDocument()

    // Sort indicator is on the name column (its sortField is `lastName`).
    const nameHeader = screen.getByRole('columnheader', { name: new RegExp(i18n.t('customersList.columns.name'), 'i') })
    expect(nameHeader).toHaveAttribute('aria-sort', 'ascending')

    // The active filter is reflected on the one views-and-filters control.
    expect(screen.getByText('1')).toBeInTheDocument()

    // And, decisively: the fetch the screen issued carried every piece of
    // that pasted state through to the API.
    await waitFor(() => expect(backend.callsTo(/^\/customers$/, 'GET').length).toBeGreaterThan(0))
    const call = backend.callsTo(/^\/customers$/, 'GET').at(-1)!
    expect(call.params.get('q')).toBe('Muster')
    expect(call.params.get('sort')).toBe('lastName:asc')
    expect(call.params.get('canton')).toBe('BE')
  })

  it('column layout and density never enter the URL, and changing density does not touch it', async () => {
    const backend = installFakeBackend([
      { match: /^\/customers$/, handler: () => customerPage([customer()]) },
    ])

    renderWithProviders(
      <>
        <LocationProbe />
        <CustomersListPage />
      </>,
      { route: '/customers?q=Muster&sort=lastName:asc' },
    )

    await screen.findByDisplayValue('Muster')
    const searchBefore = screen.getByTestId('location-search').textContent ?? ''
    expect(searchBefore).toContain('q=Muster')
    expect(searchBefore).toContain('sort=lastName')
    for (const forbidden of ['density', 'columns', 'layout', 'pinned', 'width', 'hidden']) {
      expect(searchBefore).not.toContain(forbidden)
    }

    // Cycle density via the real ActionBar control.
    await userEvent.click(screen.getByRole('button', { name: i18n.t('common.density.ariaLabel') }))

    // It persisted to the preference record …
    await waitFor(() => expect(backend.callsTo(/\/me\/preferences\/ui$/, 'PUT').length).toBeGreaterThan(0))
    const put = backend.callsTo(/\/me\/preferences\/ui$/, 'PUT').at(-1)!
    expect((put.body as { density?: string }).density).toBeDefined()

    // … and left the URL exactly as it was.
    expect(screen.getByTestId('location-search').textContent).toBe(searchBefore)
  })

  it('a pasted detail-screen URL restores the open tab', async () => {
    installFakeBackend([
      { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
      { match: /^\/customers\/c1\/(phones|emails)$/, handler: () => ({ items: [] }) },
      { match: /^\/customers\/c1\/(vehicles|external-ids)$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
    ])

    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1?tab=history' },
    )

    const historyTab = await screen.findByRole('tab', { name: new RegExp(i18n.t('customerDetail.tabs.history'), 'i') })
    expect(historyTab).toHaveAttribute('aria-selected', 'true')
  })
})
