// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { SalesListPage } from './SalesListPage'

// KAN-553 (ADR-115): a deal whose stock car changed after it was priced is
// marked in its own column, and the «Fahrzeug geändert» chip narrows the
// grid through the URL to the deals the server flags (`vehicleChanged=true`).

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

const deal = (overrides: Record<string, unknown>) => ({
  id: 'd-1',
  tenantId: 'dealership-1',
  entityType: 'offer',
  number: 'O-2026-0001',
  status: 'open',
  offerId: 'o-1',
  offerNumber: 'O-2026-0001',
  contractId: null,
  contractNumber: null,
  customerId: 'k-1',
  customerLabel: 'Ursula Vogt',
  customerLocality: 'Bern',
  vehicleLabel: 'VW Golf',
  ownerUserId: 'u-1',
  ownerLabel: 'Sara Seller',
  grossPrice: '25000.00',
  margin: null,
  documentsCount: 0,
  vehicleChangedAt: null,
  version: 1,
  createdAt: '2026-10-01T10:00:00Z',
  updatedAt: '2026-10-02T10:00:00Z',
  ...overrides,
})

const page = (items: unknown[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

describe('SalesListPage — «Fahrzeug geändert» (KAN-553)', () => {
  it('marks the flagged deal and only it', async () => {
    installFakeBackend([
      {
        match: /^\/sales\/deals$/,
        handler: () => page([deal({ vehicleChangedAt: '2026-11-11T10:00:00Z' }), deal({ id: 'd-2', number: 'O-2026-0002', offerId: 'o-2' })]),
      },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })
    const grid = within(await screen.findByRole('table'))
    const first = (await grid.findByText('O-2026-0001')).closest('[role="row"]') as HTMLElement
    const second = grid.getByText('O-2026-0002').closest('[role="row"]') as HTMLElement
    expect(within(first).getByText('Fahrzeug geändert')).toBeInTheDocument()
    expect(within(second).queryByText('Fahrzeug geändert')).toBeNull()
  })

  it('the chip sends vehicleChanged=true and lives in the URL', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([{ match: /^\/sales\/deals$/, handler: () => page([]) }])
    renderWithProviders(<SalesListPage />, { route: '/sales' })
    await waitFor(() => expect(backend.callsTo(/^\/sales\/deals$/).length).toBeGreaterThan(0))

    await user.click(screen.getByRole('button', { name: 'Fahrzeug geändert' }))
    await waitFor(() => {
      const calls = backend.callsTo(/^\/sales\/deals$/).filter((c) => c.params.get('limit') === '50')
      expect(calls[calls.length - 1].params.get('vehicleChanged')).toBe('true')
    })
  })
})
