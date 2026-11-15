// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import gridSchemas from '../api/gridSchemas.json'
import type { GridSchemaRead, StockItemPage } from '../api/types'
import { StockListPage } from './StockListPage'

// KAN-461 (FR-UI-01, U-10 — Anto, 2026-10-26: every column on every grid is
// sortable): with every column shown, every header of the Stock grid carries
// the sort affordance, and the grid shows a column for every field the
// endpoint serves (ADR-060). Exit criterion 5(c).

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

const SCHEMA = (gridSchemas as Record<string, GridSchemaRead>)['/v1/inventory/stock-items']
const EMPTY: StockItemPage = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const GRID_STORAGE_KEY = 'dms.preferences.grid.inventory.stock.list'

afterEach(() => window.localStorage.clear())

describe('Stock grid — every column sorts (KAN-461)', () => {
  it('with every column shown, every header has the sort affordance', async () => {
    // A stored layout that hides nothing: every column of the registry shows.
    window.localStorage.setItem(
      GRID_STORAGE_KEY,
      JSON.stringify({ schemaVersion: 1, sort: [], columnLayout: { order: [], hidden: [], widths: {}, pinnedLeft: [] } }),
    )
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => EMPTY }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText(/0 angezeigt/)

    const headers = screen.getAllByRole('columnheader').filter((h) => h.textContent !== '')
    expect(headers).toHaveLength(SCHEMA.fields.length)
    expect(headers.filter((h) => !h.hasAttribute('aria-sort')).map((h) => h.textContent)).toEqual([])
  })
})
