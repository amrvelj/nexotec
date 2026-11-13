// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes, useParams } from 'react-router-dom'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend } from '../../../test/fakeBackend'
import type { ValuationRead } from '../../../api/types'
import { ValuationsListPage } from './ValuationsListPage'

// KAN-569 (ADR-061: the row menu is the single definition) — the valuations
// grid's row menu carries the detail's alternative «Offerte aus dieser
// Bewertung erstellen», with the same disabled reason, and runs the same
// calls.
//
// jsdom has no layout, so the virtualizer is replaced by "render every row"
// (as in listRowActions.failure.render.test.tsx).
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

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: null, customerLabel: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null,
  vehicleVin: 'WVWZZZ1KZAW569001', finalOffer: '12000.00', deductions: [], version: 2, validFrom: '2026-10-01',
  validUntil: '2026-12-31', createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

function install(valuation: Partial<ValuationRead> = {}) {
  return installFakeBackend([
    {
      method: 'GET',
      match: /^\/valuations$/,
      handler: () => ({ items: [{ ...VALUATION, ...valuation }], nextCursor: null, total: 1, totalIsEstimate: false }),
    },
    { method: 'POST', match: /^\/sales\/offers$/, handler: () => ({ id: 'off-1', offerNumber: 'O-000017', version: 1, customerId: null, tradeInValuationId: null }) },
    {
      method: 'POST',
      match: /^\/sales\/offers\/off-1\/trade-in\/valuation$/,
      handler: () => ({ id: 'off-1', offerNumber: 'O-000017', version: 2, customerId: null, tradeInValuationId: 'val-1' }),
    },
  ])
}

function OfferWorkspaceStandIn() {
  const { id } = useParams<{ id: string }>()
  return <p>workspace {id}</p>
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/valuations" element={<ValuationsListPage />} />
      <Route path="/sales/offers/:id" element={<OfferWorkspaceStandIn />} />
    </Routes>,
    { route: '/valuations' },
  )
}

async function openRowMenu(user: ReturnType<typeof userEvent.setup>) {
  const [menuButton] = await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
  await user.click(menuButton)
}

describe('ValuationsListPage — «Offerte aus dieser Bewertung erstellen» in the row menu (KAN-569)', () => {
  it('creates the offer with the valuation as its trade-in and opens the workspace', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderPage()

    await openRowMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.createOffer')) }))

    expect(await screen.findByText('workspace off-1')).toBeTruthy()
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
    const [attach] = backend.callsTo(/\/trade-in\/valuation$/, 'POST')
    expect(attach.body).toEqual({ valuationId: 'val-1' })
  })

  it('an expired valuation’s entry is disabled with the same reason as on the detail', async () => {
    const user = userEvent.setup()
    install({ status: 'expired' } as Partial<ValuationRead>)
    renderPage()

    await openRowMenu(user)
    const entry = await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.createOffer')) })
    expect(entry.hasAttribute('disabled') || entry.getAttribute('data-disabled') === 'true' || entry.getAttribute('aria-disabled') === 'true').toBe(true)
    expect(entry.textContent).toContain(i18n.t('valuationDetail.actions.createOfferExpired'))
  })
})
