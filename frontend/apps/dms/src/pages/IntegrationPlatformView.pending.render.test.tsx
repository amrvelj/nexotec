// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { CatalogueSyncStatusRead } from '../api/types'
import { IntegrationPlatformView } from './IntegrationPlatformView'

// KAN-78 — a catalogue sync run that skipped variants (a refused Datenname)
// must say so on the fleet health board, not only in the call log.

// jsdom has no layout → TanStack Virtual renders zero rows. Same mock as
// CatalogueBrowseGrid's test, so the grid shows its rows.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const syncRow = (pendingFzKeys: CatalogueSyncStatusRead['pendingFzKeys']): CatalogueSyncStatusRead => ({
  tenantId: 'dealership-1',
  providerCode: 'auto_i_dat',
  lastFullSeedAt: '2026-10-01T02:00:00Z',
  lastDeltaCursor: '2026-10-07',
  lastSystemWatermarkDate: '2026-10-07',
  lastSystemCheckedAt: '2026-10-07T02:00:00Z',
  pendingFzKeys,
  stale: false,
})

function install(row: CatalogueSyncStatusRead) {
  return installFakeBackend([
    { match: /^\/dealerships/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { match: /^\/integrations\/connections/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { match: /^\/vehicle-mdm\/catalogue-sync-status$/, handler: () => [row] },
  ])
}

describe('IntegrationPlatformView fleet health board — pending variants (KAN-78)', () => {
  it('shows how many variants the last run left pending', async () => {
    install(
      syncRow([
        { fzKey: 'FZ100002', field: 'images', error: 'ProviderGatewayError', failedAt: '2026-10-07T02:00:00Z' },
        { fzKey: 'FZ100005', field: 'options', error: 'ProviderGatewayError', failedAt: '2026-10-07T02:00:00Z' },
      ])
    )
    renderWithProviders(<IntegrationPlatformView />)
    // The cell re-renders once the dealership names arrive, so assert on the settled tree.
    await waitFor(() => expect(screen.getByText('2 ausstehend')).toBeInTheDocument())
  })

  it('shows a dash when the last run synced every variant', async () => {
    install(syncRow([]))
    renderWithProviders(<IntegrationPlatformView />)
    await waitFor(() => expect(screen.getByText('auto_i_dat')).toBeInTheDocument())
    expect(screen.queryByText(/\d+ ausstehend/)).not.toBeInTheDocument()
  })
})
