// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { formatDate } from '../../utils/format'
import type { CustomerVehicleRead } from '../../api/types'

// KAN-644 (Anto, 2026-11-12, A): a role from a later date is upcoming «ab
// <date>», on this customer's own row and among another row's parties.
// KAN-583 (2A): «Eintrag beenden» in the row menu sends the Disconnect; (1A)
// a cancelled planned entry reads «planned, never started».

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

const HANDOVER = new Date(Date.now() + 19 * 86_400_000).toISOString()

function vehicleParty(over: Partial<CustomerVehicleRead> = {}): CustomerVehicleRead {
  return {
    id: 'party-1', customerId: 'c1', vehicleId: 'v1', role: 'owner',
    effectiveFrom: '2026-01-01T00:00:00Z', effectiveTo: null,
    vehicle: { id: 'v1', vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'V-000001', make: 'Volkswagen', model: 'Golf', modelYear: 2020, trim: null },
    otherParties: [], stockItem: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    ...over,
  } as CustomerVehicleRead
}

function install(vehicles: CustomerVehicleRead[], deleted: string[] = []) {
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: vehicles }) },
    {
      method: 'DELETE',
      match: /^\/customers\/c1\/vehicles\/[^/]+$/,
      handler: (req) => {
        deleted.push(req.pathname)
        return { __status: 204 }
      },
    },
  ])
}

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1?tab=vehicles' },
  )
}

describe('VehiclesTab — a planned handover (KAN-644, KAN-583)', () => {
  afterEach(() => cleanup())

  it('marks this customer\'s role from a later date as upcoming «ab <date>»', async () => {
    void i18n.changeLanguage('de')
    install([vehicleParty({ effectiveFrom: HANDOVER })])
    renderDetail()

    expect(await screen.findByText(i18n.t('customerDetail.vehicles.upcoming', { date: formatDate(HANDOVER, 'de-CH') }))).toBeInTheDocument()
  })

  it('names another party from a later date as upcoming, beside its name', async () => {
    void i18n.changeLanguage('de')
    install([
      vehicleParty({
        effectiveTo: HANDOVER,
        otherParties: [{ customerId: 'c-meier', role: 'owner', displayName: 'Anna Meier', effectiveFrom: HANDOVER }],
      }),
    ])
    renderDetail()

    const link = await screen.findByRole('link', { name: 'Anna Meier' })
    expect(link.parentElement?.textContent).toContain(i18n.t('customerDetail.vehicles.upcoming', { date: formatDate(HANDOVER, 'de-CH') }))
  })

  it('«Eintrag beenden» in the row menu sends the Disconnect of that row', async () => {
    void i18n.changeLanguage('de')
    const deleted: string[] = []
    install([vehicleParty({ id: 'party-7', effectiveFrom: HANDOVER })], deleted)
    renderDetail()

    const row = await screen.findByRole('row', { name: /WVWZZZ1JZXW000001/ })
    await userEvent.click(within(row).getByRole('button', { name: i18n.t('common.rowActionsLabel') }))
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('customerDetail.vehicles.endEntry') }))
    await vi.waitFor(() => expect(deleted).toEqual(['/customers/c1/vehicles/party-7']))
  })

  it('a cancelled planned entry reads «planned, never started» and offers no end', async () => {
    void i18n.changeLanguage('de')
    install([vehicleParty({ effectiveFrom: HANDOVER, effectiveTo: HANDOVER })])
    renderDetail()

    expect(await screen.findByText(i18n.t('customerDetail.vehicles.plannedNeverStarted'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('customerDetail.vehicles.upcoming', { date: formatDate(HANDOVER, 'de-CH') }))).toBeNull()
  })
})
