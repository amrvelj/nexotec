// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { SalesListPage } from './SalesListPage'

// KAN-200 — the responsible seller is a column (ADR-060: a stored field is
// on screen) and the unassigned queue FR-A-37 names is a chip: deals whose
// seller left and were handed to nobody, filtered through the URL (ADR-056).

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

function deal(overrides: Record<string, unknown>) {
  return {
    id: 'd-1',
    tenantId: 'dealership-1',
    entityType: 'contract',
    number: 'V-2026-0001',
    status: 'pending',
    offerId: 'o-1',
    offerNumber: 'O-2026-0001',
    contractId: 'c-1',
    contractNumber: 'V-2026-0001',
    customerId: 'k-1',
    customerLabel: 'Ursula Vogt',
    customerLocality: 'Bern',
    vehicleLabel: 'VW Golf',
    ownerUserId: 'u-1',
    ownerLabel: 'Sara Seller',
    grossPrice: '25000.00',
    margin: null,
    documentsCount: 0,
    version: 1,
    createdAt: '2026-10-01T10:00:00Z',
    updatedAt: '2026-10-02T10:00:00Z',
    ...overrides,
  }
}

const page = (items: unknown[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

describe('SalesListPage — the responsible seller and the unassigned queue (KAN-200)', () => {
  it('shows who is responsible and "—" for a deal in the queue', async () => {
    installFakeBackend([
      { match: /^\/sales\/deals$/, handler: () => page([deal({}), deal({ id: 'd-2', number: 'V-2026-0002', contractId: 'c-2', ownerUserId: null, ownerLabel: null })]) },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })
    const grid = within(await screen.findByRole('table'))
    expect(grid.getByRole('columnheader', { name: /Verantwortlich/ })).toBeInTheDocument()
    expect(await grid.findByText('Sara Seller')).toBeInTheDocument()
    const second = grid.getByText('V-2026-0002').closest('[role="row"]')!
    expect(within(second).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('filters the unassigned queue through the chip and the URL', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([{ match: /^\/sales\/deals$/, handler: () => page([]) }])
    renderWithProviders(<SalesListPage />, { route: '/sales?owner=unassigned' })
    await waitFor(() => expect(backend.callsTo(/^\/sales\/deals$/).some((c) => c.params.get('unassigned') === 'true')).toBe(true))

    await user.click(screen.getByRole('button', { name: 'Nicht zugewiesen' }))
    await waitFor(() => {
      const calls = backend.callsTo(/^\/sales\/deals$/).filter((c) => c.params.get('limit') === '50')
      expect(calls[calls.length - 1].params.get('unassigned')).toBeNull()
    })
  })
})
