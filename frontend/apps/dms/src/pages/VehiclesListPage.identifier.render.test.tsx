// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { VehicleMdmRead, VehicleSearchResult } from '../api/types'
import { VehiclesListPage } from './VehiclesListPage'

// KAN-82 — an identifier hit no longer carries a page of its own (it was an
// unrelated one, and global search showed it). FR-V-06 still says the hit
// "resolves above the grid … which stays where it was", so the screen
// reads the unfiltered list for the grid itself. An identifier that
// matches nothing says so instead of showing the whole fleet.

// jsdom has no layout; render every row (same shim as the paging test).
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
  return {
    id: `veh-${n}`, vehicleNumber: `F-00000${n}`, vin: `VIN0000000000000${n}`, stammnummer: null,
    typeApprovalNumber: null, firstRegistrationDate: null, catalogueVariantId: null,
    catalogueMatchStatus: 'unverified', vehicleStatus: 'active', mergedIntoVehicleId: null, version: 1,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
}

const FLEET = [makeVehicle(1), makeVehicle(2), makeVehicle(3)]
const EMPTY_PAGE = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

const SEARCH: Record<string, VehicleSearchResult> = {
  '': { resolved: null, pickerCandidates: [], filtered: { items: FLEET, nextCursor: null, total: 3, totalIsEstimate: false } },
  'ZH 12345': { resolved: { ...FLEET[1], currentPlate: 'ZH 12345' }, pickerCandidates: [], filtered: EMPTY_PAGE },
  'AG 55555': { resolved: null, pickerCandidates: [], filtered: EMPTY_PAGE },
}

function renderAt(route: string) {
  const backend = installFakeBackend([
    { match: /^\/vehicle-mdm\/search$/, handler: (req) => SEARCH[req.params.get('q') ?? ''] },
  ])
  renderWithProviders(<VehiclesListPage />, { route })
  return backend
}

const tr = (key: string) => i18n.t(key)

afterEach(() => cleanup())

describe('VehiclesListPage — an identifier resolves above a grid that stays where it was (KAN-82)', () => {
  it('a resolved plate shows the match above the unfiltered list, in the chosen sort', async () => {
    const backend = renderAt('/vehicles?q=ZH%2012345&sort=vin:asc')

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(tr('vehiclesList.resolved.title'))).toBeInTheDocument()
    expect(within(alert).getByText('F-000002')).toBeInTheDocument()

    // The grid below is the whole list — the matched car included, so its
    // VIN shows twice: once in the match, once in its grid row.
    expect(await screen.findByText(FLEET[0].vin)).toBeInTheDocument()
    expect(screen.getByText(FLEET[2].vin)).toBeInTheDocument()
    expect(screen.getAllByText(FLEET[1].vin)).toHaveLength(2)
    expect(screen.queryByText(tr('vehiclesList.emptyFilteredState.title'))).not.toBeInTheDocument()
    const calls = backend.callsTo(/^\/vehicle-mdm\/search$/, 'GET')
    expect(calls.map((c) => c.params.get('q'))).toEqual(expect.arrayContaining(['ZH 12345', '']))
    // The grid under the hit is the user's grid: it keeps their sort.
    expect(calls.filter((c) => c.params.get('q') === '').every((c) => c.params.get('sort') === 'vin:asc')).toBe(true)
  })

  it('an identifier that matches nothing shows the no-match state, not the fleet', async () => {
    renderAt('/vehicles?q=AG%2055555')

    expect(await screen.findByText(tr('vehiclesList.emptyFilteredState.title'))).toBeInTheDocument()
    for (const v of FLEET) expect(screen.queryByText(v.vin)).not.toBeInTheDocument()
    expect(screen.queryByText(tr('vehiclesList.resolved.title'))).not.toBeInTheDocument()
  })
})
