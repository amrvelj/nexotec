// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import { CustomerDetailPage } from './CustomerDetailPage'

// KAN-371 (Anto, option A): GET /customers/{id}/audit-log names the ids its
// events carry in `labels`; the page hands them to the History tab beside the
// labels the customer record carries (KAN-336). This pins the wiring from the
// endpoint to the screen — HistoryTab's own tests feed `idLabels` directly.

const OWN_BETRIEB = '01a1487e-0000-7000-8000-0000000000d1'
const GONE_BETRIEB = '01a1487e-0000-7000-8000-0000000000d2'
const ACTOR = '01a1487e-0000-7000-8000-0000000000e1'

function routes() {
  return [
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      match: /^\/customers\/c1\/audit-log$/,
      handler: () => ({
        items: [
          {
            id: 'ev-1',
            entityType: 'customer',
            entityId: 'c1',
            tenantId: 'group-1',
            action: 'update',
            actorId: ACTOR,
            before: { dealership_id: GONE_BETRIEB },
            after: { dealership_id: OWN_BETRIEB },
            reason: null,
            createdAt: '2026-10-01T10:00:00Z',
          },
        ],
        nextCursor: null,
        labels: { [OWN_BETRIEB]: 'Garage Muster AG', [ACTOR]: 'Anna Muster' },
      }),
    },
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
  ]
}

describe('customer detail — the History names what the endpoint named (KAN-371)', () => {
  it('renders the Betrieb and the actor by name, and an id it did not name as unknown', async () => {
    installFakeBackend(routes())
    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1?tab=history' },
    )

    const unknown = i18n.t('customerDetail.history.unknownReference')
    expect(await screen.findByText(new RegExp(`${unknown} → Garage Muster AG`))).toBeInTheDocument()
    expect(screen.getByText(/Anna Muster/)).toBeInTheDocument()
    for (const id of [OWN_BETRIEB, GONE_BETRIEB, ACTOR]) {
      expect(screen.queryByText(new RegExp(id))).not.toBeInTheDocument()
    }
  })
})
