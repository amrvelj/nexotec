// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, within } from '@testing-library/react'
import i18n, { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { customer } from '../../../../test/fixtures'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { formatDate } from '../../../../utils/format'
import type { CustomerVehicleRead } from '../../../../api/types'

// KAN-49 / FR-19 — the Vehicles tab must name the OTHER parties on the
// same car (owner/keeper/driver can all be true at once, the leased-
// company-car case), and link a vehicle currently in the group's own
// stock to its stock item.

// jsdom has no layout, so TanStack Virtual windows down to zero rows —
// mock it to "render every row" so these can assert on cell content
// (same shim CustomersListPage.columns.render.test.tsx and ui-kit's own
// DataGrid.rowActivate.render.test.tsx use).
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

function vehicleParty(over: Partial<CustomerVehicleRead> = {}): CustomerVehicleRead {
  return {
    id: 'party-1',
    customerId: 'c1',
    vehicleId: 'v1',
    role: 'driver',
    effectiveFrom: '2026-01-01T00:00:00Z',
    effectiveTo: null,
    vehicle: { id: 'v1', vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'V-000001', make: 'Volkswagen', model: 'Golf', modelYear: 2020, trim: null },
    otherParties: [],
    stockItem: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...over,
  } as CustomerVehicleRead
}

function install(vehicles: CustomerVehicleRead[]) {
  return installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: vehicles }) },
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

describe('VehiclesTab (KAN-49 / FR-19)', () => {
  it('the leased-company-car case: both other parties are named with their roles', async () => {
    install([
      vehicleParty({
        role: 'driver',
        otherParties: [
          { customerId: 'owner-1', role: 'owner', displayName: 'Leasing AG' },
          { customerId: 'keeper-1', role: 'keeper', displayName: 'Muster GmbH' },
        ],
      }),
    ])
    renderDetail()

    expect(await screen.findByText('Leasing AG')).toBeInTheDocument()
    expect(screen.getByText('Muster GmbH')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Leasing AG' })).toHaveAttribute('href', '/customers/owner-1')
    expect(screen.getByRole('link', { name: 'Muster GmbH' })).toHaveAttribute('href', '/customers/keeper-1')
  })

  it('a vehicle with a single party renders no others block — no "—", no "null"', async () => {
    install([vehicleParty({ otherParties: [] })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    const otherPartiesHeader = screen.getByRole('columnheader', { name: i18n.t('customerDetail.vehicles.columns.otherParties') })
    const columnIndex = Array.from(otherPartiesHeader.parentElement?.children ?? []).indexOf(otherPartiesHeader)
    const row = screen.getByRole('row', { name: /WVWZZZ1JZXW000001/ })
    const cell = within(row).getAllByRole('cell')[columnIndex]
    expect(cell).toHaveTextContent('')
    expect(within(cell).queryByText('—')).not.toBeInTheDocument()
    expect(within(cell).queryByText(/null/i)).not.toBeInTheDocument()
  })

  it("this customer's own closed role still renders, marked ended", async () => {
    install([vehicleParty({ effectiveTo: '2026-06-01T00:00:00Z' })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    // Assert the actual formatted date appears in the "until" cell, not
    // just "no '—' placeholder anywhere" — that weaker assertion would
    // also pass for a regression that renders an empty string instead of
    // the date (KAN-49 review).
    const untilHeader = screen.getByRole('columnheader', { name: i18n.t('customerDetail.vehicles.columns.until') })
    const columnIndex = Array.from(untilHeader.parentElement?.children ?? []).indexOf(untilHeader)
    const row = screen.getByRole('row', { name: /WVWZZZ1JZXW000001/ })
    const cell = within(row).getAllByRole('cell')[columnIndex]
    const expected = formatDate('2026-06-01T00:00:00Z', toSwissLocale(i18n.language as SupportedLanguage))
    expect(cell).toHaveTextContent(expected)
    expect(within(cell).queryByText('—')).not.toBeInTheDocument()
  })

  it('a vehicle currently in the group\'s own stock links to its stock item', async () => {
    install([vehicleParty({ stockItem: { id: 'stock-1', stockNumber: 'S-000042' } })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    const stockLink = screen.getByTitle(i18n.t('customerDetail.vehicles.inStockTitle', { stockNumber: 'S-000042' }))
    expect(stockLink).toHaveAttribute('href', '/stock/stock-1')
  })

  it('KAN-339: a party whose vehicle is not in the register renders, saying so — no "null"', async () => {
    install([
      vehicleParty({
        vehicle: { id: 'v-gone', vin: null, vehicleNumber: null, make: null, model: null, modelYear: null, trim: null },
      }),
    ])
    renderDetail()

    const label = await screen.findByText(i18n.t('customerDetail.vehicles.unknownVehicle'))
    const row = label.closest('[role="row"]') as HTMLElement
    const vinHeader = screen.getByRole('columnheader', { name: i18n.t('customerDetail.vehicles.columns.vin') })
    const columnIndex = Array.from(vinHeader.parentElement?.children ?? []).indexOf(vinHeader)
    expect(within(row).getAllByRole('cell')[columnIndex]).toHaveTextContent('—')
    expect(within(row).queryByText(/null/i)).not.toBeInTheDocument()
  })

  it('no stock item -> no stock link, and no crash', async () => {
    install([vehicleParty({ stockItem: null })])
    renderDetail()

    await screen.findByText('WVWZZZ1JZXW000001')
    expect(screen.queryByRole('link', { name: /S-/ })).not.toBeInTheDocument()
  })

  // KAN-347 (ADR-064) — a row a customer merge kept closed at its own start
  // (KAN-177) is marked as such in the UI language and links to the survivor;
  // an ordinary ended role carries no mark.
  describe('a row a customer merge kept closed', () => {
    afterEach(async () => {
      cleanup()
      await i18n.changeLanguage('de')
    })

    it.each(['de', 'fr', 'it', 'en'] as const)('is marked merged and links to the survivor (%s)', async (language) => {
      await i18n.changeLanguage(language)
      install([
        vehicleParty({ effectiveFrom: '2026-01-01T00:00:00Z', effectiveTo: '2026-01-01T00:00:00Z', mergedIntoCustomerId: 'survivor-1' }),
      ])
      renderDetail()

      await screen.findByText('WVWZZZ1JZXW000001')
      const marker = screen.getByText(i18n.t('customerDetail.vehicles.mergedInto'))
      expect(marker.closest('a')).toHaveAttribute('href', '/customers/survivor-1')
      expect(marker.closest('a')).toHaveAttribute('title', i18n.t('customerDetail.vehicles.mergedIntoTitle'))
    })

    it('an ordinary ended role is not marked', async () => {
      install([vehicleParty({ effectiveTo: '2026-06-01T00:00:00Z', mergedIntoCustomerId: null })])
      renderDetail()

      await screen.findByText('WVWZZZ1JZXW000001')
      expect(screen.queryByText(i18n.t('customerDetail.vehicles.mergedInto'))).not.toBeInTheDocument()
    })
  })

  // KAN-680 (Anto, 2026-11-13, option A) — a row a vehicle merge kept as
  // history (KAN-672) says which record the car lives on now, with its VIN,
  // links that record and offers no «Eintrag beenden».
  describe('a row a vehicle merge kept as history', () => {
    afterEach(async () => {
      cleanup()
      await i18n.changeLanguage('de')
    })

    it.each(['de', 'fr', 'it', 'en'] as const)('names the surviving VIN and links the record (%s)', async (language) => {
      await i18n.changeLanguage(language)
      install([
        vehicleParty({
          effectiveFrom: '2026-01-01T00:00:00Z',
          effectiveTo: '2026-01-01T00:00:00Z',
          mergedIntoVehicleId: 'survivor-car',
          mergedIntoVehicleVin: 'WVWZZZ1JZXW000099',
        }),
      ])
      renderDetail()

      await screen.findByText('WVWZZZ1JZXW000001')
      const marker = screen.getByText(i18n.t('customerDetail.vehicles.mergedIntoVehicle', { vin: 'WVWZZZ1JZXW000099' }))
      expect(marker.textContent).toContain('WVWZZZ1JZXW000099')
      expect(marker.closest('a')).toHaveAttribute('href', '/vehicles/survivor-car')
      expect(marker.closest('a')).toHaveAttribute('title', i18n.t('customerDetail.vehicles.mergedIntoVehicleTitle'))
      expect(screen.queryByText(i18n.t('customerDetail.vehicles.mergedInto'))).not.toBeInTheDocument()
    })

    it('an ordinary ended role carries no vehicle-merge mark', async () => {
      install([vehicleParty({ effectiveTo: '2026-06-01T00:00:00Z', mergedIntoVehicleId: null, mergedIntoVehicleVin: null })])
      renderDetail()

      await screen.findByText('WVWZZZ1JZXW000001')
      expect(screen.queryByText(/WVWZZZ1JZXW000099/)).not.toBeInTheDocument()
    })
  })
})
