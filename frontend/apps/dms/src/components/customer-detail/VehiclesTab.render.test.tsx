// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, within } from '@testing-library/react'
import i18n, { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { formatDate } from '../../utils/format'
import type { CustomerVehicleRead } from '../../api/types'

// KAN-49 / FR-19 — the Vehicles tab must name the OTHER parties on the
// same car (owner/keeper/driver can all be true at once, the leased-
// company-car case), and link a vehicle currently in the group's own
// stock to its stock item.

// jsdom has no layout, so TanStack Virtual windows down to zero rows —
// mock it to "render every row" so these can assert on cell content
// (same shim CustomersListPage.columns.render.test.tsx and ui-kit's own
// DataGrid.rowActivate.render.test.tsx use).
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

function vehicleParty(over: Partial<CustomerVehicleRead> = {}): CustomerVehicleRead {
  return {
    id: 'party-1',
    customerId: 'c1',
    vehicleId: 'v1',
    role: 'driver',
    effectiveFrom: '2026-01-01T00:00:00Z',
    effectiveTo: null,
    vehicle: { id: 'v1', vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'V-000001', make: 'Volkswagen', model: 'Golf', modelYear: 2020, trim: null },
    otherParties: [],
    stockItem: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...over,
  } as CustomerVehicleRead
}

function install(vehicles: CustomerVehicleRead[]) {
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: vehicles }) },
  ])
}

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1?tab=vehicles' },
  )
}

describe('VehiclesTab (KAN-49 / FR-19)', () => {
  it('the leased-company-car case: both other parties are named with their roles', async () => {
    install([
      vehicleParty({
        role: 'driver',
        otherParties: [
          { customerId: 'owner-1', role: 'owner', displayName: 'Leasing AG' },
          { customerId: 'keeper-1', role: 'keeper', displayName: 'Muster GmbH' },
        ],
      }),
    ])
    renderDetail()

    expect(await screen.findByText('Leasing AG')).toBeInTheDocument()
    expect(screen.getByText('Muster GmbH')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Leasing AG' })).toHaveAttribute('href', '/customers/owner-1')
    expect(screen.getByRole('link', { name: 'Muster GmbH' })).toHaveAttribute('href', '/customers/keeper-1')
  })

  it('a vehicle with a single party renders no others block — no "—", no "null"', async () => {
    install([vehicleParty({ otherParties: [] })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    const otherPartiesHeader = screen.getByRole('columnheader', { name: i18n.t('customerDetail.vehicles.columns.otherParties') })
    const columnIndex = Array.from(otherPartiesHeader.parentElement?.children ?? []).indexOf(otherPartiesHeader)
    const row = screen.getByRole('row', { name: /WVWZZZ1JZXW000001/ })
    const cell = within(row).getAllByRole('cell')[columnIndex]
    expect(cell).toHaveTextContent('')
    expect(within(cell).queryByText('—')).not.toBeInTheDocument()
    expect(within(cell).queryByText(/null/i)).not.toBeInTheDocument()
  })

  it("this customer's own closed role still renders, marked ended", async () => {
    install([vehicleParty({ effectiveTo: '2026-06-01T00:00:00Z' })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    // Assert the actual formatted date appears in the "until" cell, not
    // just "no '—' placeholder anywhere" — that weaker assertion would
    // also pass for a regression that renders an empty string instead of
    // the date (KAN-49 review).
    const untilHeader = screen.getByRole('columnheader', { name: i18n.t('customerDetail.vehicles.columns.until') })
    const columnIndex = Array.from(untilHeader.parentElement?.children ?? []).indexOf(untilHeader)
    const row = screen.getByRole('row', { name: /WVWZZZ1JZXW000001/ })
    const cell = within(row).getAllByRole('cell')[columnIndex]
    const expected = formatDate('2026-06-01T00:00:00Z', toSwissLocale(i18n.language as SupportedLanguage))
    expect(cell).toHaveTextContent(expected)
    expect(within(cell).queryByText('—')).not.toBeInTheDocument()
  })

  it('a vehicle currently in the group\'s own stock links to its stock item', async () => {
    install([vehicleParty({ stockItem: { id: 'stock-1', stockNumber: 'S-000042' } })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    const stockLink = screen.getByTitle(i18n.t('customerDetail.vehicles.inStockTitle', { stockNumber: 'S-000042' }))
    expect(stockLink).toHaveAttribute('href', '/stock/stock-1')
  })

  it('no stock item -> no stock link, and no crash', async () => {
    install([vehicleParty({ stockItem: null })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    expect(screen.queryByRole('link', { name: /S-/ })).not.toBeInTheDocument()
  })
})
