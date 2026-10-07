// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { VehicleMdmRead, VehicleSearchResult } from '../api/types'
import { VehiclesListPage } from './VehiclesListPage'

// KAN-161 — the Vehicles list sorted nothing server-side and never loaded
// past its first page (hasNextPage={false}, nextCursor ignored).

// jsdom has no layout, so TanStack Virtual windows down to zero rows. Same
// "render every row" shim as CustomersListPage.columns.render.test.tsx —
// with every row rendered, the last row is within 10 of the end and the
// grid asks for the next page (FR-UI-04), exactly as a scroll would.
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

function makeVehicle(n: number): VehicleMdmRead {
  const number = `F-${String(n).padStart(6, '0')}`
  return {
    id: `00000000-0000-7000-8000-${String(n).padStart(12, '0')}`,
    vehicleNumber: number,
    vin: `VIN${String(n).padStart(14, '0')}`,
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: null,
    catalogueVariantId: null,
    catalogueMatchStatus: 'unverified',
    vehicleStatus: 'active',
    mergedIntoVehicleId: null,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  } as VehicleMdmRead
}

const PAGE_ONE = [makeVehicle(1), makeVehicle(2)]
const PAGE_TWO = [makeVehicle(3)]

function page(items: VehicleMdmRead[], nextCursor: string | null): VehicleSearchResult {
  return { resolved: null, pickerCandidates: [], filtered: { items, nextCursor, total: 3, totalIsEstimate: false } }
}

const searchRoute: FakeRoute = {
  method: 'GET',
  match: /^\/vehicle-mdm\/search$/,
  handler: (req) => (req.params.get('cursor') === 'cursor-2' ? page(PAGE_TWO, null) : page(PAGE_ONE, 'cursor-2')),
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

afterEach(() => cleanup())

describe('VehiclesListPage — server-side sort and cursor paging (KAN-161)', () => {
  it('loads the next page by the cursor the first page returned, and shows its rows', async () => {
    const backend = installFakeBackend([searchRoute])

    renderWithProviders(<VehiclesListPage />, { route: '/vehicles' })

    expect(await screen.findByText('F-000003')).toBeInTheDocument()
    expect(screen.getByText('F-000001')).toBeInTheDocument()
    const calls = backend.callsTo(/^\/vehicle-mdm\/search$/, 'GET')
    expect(calls[0].params.get('cursor')).toBeNull()
    expect(calls.some((c) => c.params.get('cursor') === 'cursor-2')).toBe(true)
  })

  it('clicking a column header sends sort to the API and writes it to the URL', async () => {
    const backend = installFakeBackend([searchRoute])

    renderWithProviders(
      <>
        <LocationProbe />
        <VehiclesListPage />
      </>,
      { route: '/vehicles' },
    )
    await screen.findByText('F-000001')

    const vinHeader = screen.getAllByRole('columnheader').find((h) => h.getAttribute('aria-sort') !== null && /VIN/i.test(h.textContent ?? ''))
    expect(vinHeader).toBeDefined()
    await userEvent.click(vinHeader!)

    await waitFor(() => {
      const call = backend.callsTo(/^\/vehicle-mdm\/search$/, 'GET').at(-1)
      expect(call?.params.get('sort')).toBe('vin:asc')
    })
    expect(screen.getByTestId('location-search').textContent).toContain('sort=vin%3Aasc')
  })

  it('every column offers a server-side sort', async () => {
    installFakeBackend([searchRoute])

    renderWithProviders(<VehiclesListPage />, { route: '/vehicles' })
    await screen.findByText('F-000001')

    const sortable = screen.getAllByRole('columnheader').filter((h) => h.getAttribute('aria-sort') !== null)
    expect(sortable).toHaveLength(5)
  })
})
