// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { waitFor } from '@testing-library/react'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { CustomersListPage } from './CustomersListPage'

// KAN-200 (FR-A-37) — the advisor column filters: one colleague's customers
// (`advisorUserId`), or the unassigned queue a leaver's hand-over to nobody
// fills (`unassigned=true`, the API's own flag — never an id).

const page = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const routes = () => [
  { match: /^\/customers$/, handler: () => page },
  { match: /^\/customers\/advisor-options$/, handler: () => ({ items: [{ id: 'u-9', label: 'Boss Meier' }] }) },
]

describe('CustomersListPage — advisor filter (KAN-200)', () => {
  it('sends advisorUserId for a colleague and unassigned=true for the queue', async () => {
    const forColleague = { id: 'p1', fieldId: 'advisor', type: 'select', condition: 'is', value: 'u-9' }
    const backend = installFakeBackend(routes())
    renderWithProviders(<CustomersListPage />, { route: `/customers?filters=${encodeURIComponent(JSON.stringify([forColleague]))}` })
    await waitFor(() => expect(backend.callsTo(/^\/customers$/).some((c) => c.params.get('advisorUserId') === 'u-9')).toBe(true))
    expect(backend.callsTo(/^\/customers$/).some((c) => c.params.get('unassigned') === 'true')).toBe(false)
  })

  it('maps the unassigned option to the queue flag', async () => {
    const forQueue = { id: 'p1', fieldId: 'advisor', type: 'select', condition: 'is', value: '__unassigned__' }
    const backend = installFakeBackend(routes())
    renderWithProviders(<CustomersListPage />, { route: `/customers?filters=${encodeURIComponent(JSON.stringify([forQueue]))}` })
    await waitFor(() => expect(backend.callsTo(/^\/customers$/).some((c) => c.params.get('unassigned') === 'true')).toBe(true))
    expect(backend.callsTo(/^\/customers$/).some((c) => c.params.has('advisorUserId'))).toBe(false)
  })
})
