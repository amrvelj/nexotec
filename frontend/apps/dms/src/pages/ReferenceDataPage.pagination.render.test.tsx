// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// KAN-59: the endpoint caps a page at 100 (Settings.pagination_max_limit).
// `country` alone is ~250 rows, so a naive single `limit=200` request 422s
// for every list, always — reproduced live on staging. This fakes a list
// with 150 rows, split into two real pages by the SAME cursor mechanics the
// real backend uses, and proves the page walks both pages rather than
// truncating to the first 100 or refusing to load at all.

const TOTAL_ROWS = 150
const PAGE_SIZE = 100

function makeRow(i: number) {
  const code = `code_${String(i).padStart(3, '0')}`
  return {
    id: `fuel_type-${code}`,
    listCode: 'fuel_type',
    valueCode: code,
    labelDe: `Deutsch ${i}`,
    labelFr: `Français ${i}`,
    labelIt: `Italiano ${i}`,
    labelEn: `English ${i}`,
    sortOrder: i,
    active: true,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    updatedBy: null,
  }
}

const ALL_ROWS = Array.from({ length: TOTAL_ROWS }, (_, i) => makeRow(i))

const paginatedFuelTypeRoute: FakeRoute = {
  method: 'GET',
  match: /\/reference-data\/fuel_type$/,
  handler: (req) => {
    const cursor = req.params.get('cursor')
    const start = cursor ? Number(cursor) : 0
    const limit = Number(req.params.get('limit') ?? PAGE_SIZE)
    if (limit > PAGE_SIZE) {
      // Mirrors the real backend's own validation (Settings.pagination_max_limit
      // = 100) — if the page under test ever regresses back to requesting
      // more than the cap, this fake 422s the exact same way staging did.
      return {
        error: {
          code: 'unprocessable_entity',
          message: 'Request validation failed.',
          details: { errors: [{ type: 'less_than_equal', loc: ['query', 'limit'], msg: 'Input should be less than or equal to 100' }] },
        },
      }
    }
    const page = ALL_ROWS.slice(start, start + limit)
    const end = start + page.length
    return { items: page, nextCursor: end < TOTAL_ROWS ? String(end) : null }
  },
}

describe('ReferenceDataPage — pagination (KAN-59)', () => {
  it('walks every page of a list larger than one page and shows every row, not just the first 100', async () => {
    installFakeBackend([paginatedFuelTypeRoute])

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference?list=fuel_type' })

    // The first row of the first page.
    await screen.findByText('code_000')
    // A row that only exists on the SECOND page (index 149, 0-based) — this
    // is the assertion that would fail if the fix regressed to one page.
    await waitFor(() => expect(screen.getByText('code_149')).toBeInTheDocument())

    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument()
  })
})
