// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { SalesListPage } from './SalesListPage'
import { ValuationsListPage } from '../modules/valuation/pages/ValuationsListPage'
import { CatalogueBrowseGrid } from '../components/catalogue/CatalogueBrowseGrid'

// KAN-701 — the list screens read and write their `?sort=` link through the
// kit's `sortSpecFromQueryParam` / `sortSpecToQueryParam` (ADR-056), not a
// copy of their own. A pasted link opens the same order it named: the grid
// asks the API for exactly that sort, levels and directions included. The
// Customers, Users, Stock, Vehicles and Account-recovery screens have their
// own `?sort=` render tests; these three had none.

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

const EMPTY_PAGE = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }

describe('a pasted ?sort= link opens the order it names (KAN-701)', () => {
  it('Sales asks for the pasted sort', async () => {
    const backend = installFakeBackend([{ match: /^\/sales\/deals$/, handler: () => EMPTY_PAGE }])
    renderWithProviders(<SalesListPage />, { route: '/sales?sort=number:asc,updatedAt:desc' })

    await waitFor(() =>
      expect(backend.callsTo(/^\/sales\/deals$/).map((call) => call.params.get('sort'))).toContain('number:asc,updatedAt:desc'),
    )
  })

  it('Valuations asks for the pasted sort', async () => {
    const backend = installFakeBackend([{ match: /^\/valuations$/, handler: () => EMPTY_PAGE }])
    renderWithProviders(<ValuationsListPage />, { route: '/valuations?sort=mileage:asc,valuationNumber:desc' })

    await waitFor(() =>
      expect(backend.callsTo(/^\/valuations$/).map((call) => call.params.get('sort'))).toContain(
        'mileage:asc,valuationNumber:desc',
      ),
    )
  })

  it('the catalogue asks the provider for the pasted sort on the fields it sorts, and drops another', async () => {
    const backend = installFakeBackend([
      {
        match: /\/catalogue\/brands$/,
        handler: () => ({ items: [{ name: 'Volkswagen', brandNr: '2', models: ['Golf'] }], state: 'ok' }),
      },
      { match: /\/catalogue\/model-groups$/, handler: () => ({ items: [], state: 'ok' }) },
      { match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, state: 'ok', coded: {}, numeric: {} }) },
      { match: /\/catalogue\/variants$/, handler: () => ({ ...EMPTY_PAGE, browseAvailable: true, state: 'ok' }) },
      { match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<CatalogueBrowseGrid mode="build" />, {
      route: '/catalogue?brand=Volkswagen&sort=kw:desc,name:asc,doors:asc',
    })

    await waitFor(() =>
      expect(backend.callsTo(/\/catalogue\/variants$/).map((call) => call.params.get('sort'))).toContain('kw:desc,doors:asc'),
    )
    // A field the identAPI does not sort is narrowed away, never sent (ui-grid.md).
    expect(backend.callsTo(/\/catalogue\/variants$/).every((call) => !call.params.get('sort')?.includes('name'))).toBe(true)
  })
})
