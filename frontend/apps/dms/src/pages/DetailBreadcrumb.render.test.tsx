// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { waitFor } from '@testing-library/react'
import { BreadcrumbProvider } from '@nexotec/ui-kit'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { StockItemRead, VehicleMdmRead } from '../api/types'
import { VehicleDetailPage } from './VehicleDetailPage'
import { StockDetailPage } from './StockDetailPage'

// KAN-63 — both pages used to pass the raw route param (the UUIDv7 primary
// key) straight into useSetBreadcrumb, so the breadcrumb's last segment was
// e.g. "01a0af73-6570-7571-9170-ec0f5b17f9ac" instead of the same
// human-readable label the page's own <DetailHeader title> already renders
// correctly. Asserted here against the actual segments array passed to
// BreadcrumbContext — the shared Topbar that turns it into visible text is
// a pure prop-renderer with nothing of its own to break, so this is the
// meaningful boundary to test at.

const UUID_LIKE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

function captureBreadcrumbs() {
  const calls: string[][] = []
  const Capture = ({ children }: { children: React.ReactNode }) => (
    <BreadcrumbProvider value={(segments) => calls.push(segments)}>{children}</BreadcrumbProvider>
  )
  return { calls, Capture }
}

describe('Vehicle and Stock detail breadcrumbs never show the raw UUID (KAN-63)', () => {
  it('VehicleDetailPage resolves the breadcrumb to vehicleNumber, not the route id', async () => {
    const vehicleId = '01a0af73-6570-7571-9170-ec0f5b17f9ac'
    const vehicle: VehicleMdmRead = {
      id: vehicleId,
      vehicleNumber: 'F-000002',
      vin: 'WBA4Y9F55LCE89GLA',
      stammnummer: null,
      typeApprovalNumber: null,
      catalogueVariantId: null,
      mergedIntoVehicleId: null,
      catalogueMatchStatus: 'unverified',
      vehicleStatus: 'active',
      firstRegistrationDate: null,
      version: 1,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
    }
    installFakeBackend([
      { match: new RegExp(`^/vehicle-mdm/${vehicleId}$`), handler: () => vehicle },
      { match: new RegExp(`^/vehicle-mdm/${vehicleId}/plates$`), handler: () => [] },
      { match: new RegExp(`^/vehicle-mdm/${vehicleId}/odometer-readings$`), handler: () => [] },
      { match: new RegExp(`^/vehicle-mdm/${vehicleId}/accessories$`), handler: () => [] },
      { match: new RegExp(`^/vehicle-mdm/${vehicleId}/party-roles$`), handler: () => [] },
    ])

    const { calls, Capture } = captureBreadcrumbs()
    renderWithProviders(
      <Capture>
        <Routes>
          <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
        </Routes>
      </Capture>,
      { route: `/vehicles/${vehicleId}` },
    )

    await waitFor(() => expect(calls.at(-1)).toContain('F-000002'))
    const finalSegments = calls.at(-1)!
    expect(finalSegments.some((s) => UUID_LIKE.test(s))).toBe(false)
  })

  it('StockDetailPage resolves the breadcrumb to vehicleLabel, not the route id', async () => {
    const stockId = '01a0616b-71e0-74ca-9877-29a28ec526ee'
    const item: StockItemRead = {
      id: stockId,
      stockNumber: 'S-000002',
      vehicleLabel: 'VW Golf 2019',
      vin: null,
      vehicleId: null,
      condition: 'used',
      lifecycleStatus: 'in_stock',
      reservationState: 'none',
      bodyStyle: null,
      exteriorColour: null,
      odometerKm: null,
      firstRegistrationDate: null,
      basePrice: null,
      listPrice: null,
      effectivePrice: null,
      landedCost: null,
      notionalInputTaxApplicable: null,
      notionalInputTaxRate: null,
      notionalInputTaxAmount: null,
      notionalInputTaxOverridden: false,
      purchasePrice: null,
      purchaseDate: null,
      purchaseInvoiceRef: null,
      supplierName: null,
      supplierIsVatRegistered: null,
      orderDate: null,
      expectedDelivery: null,
      inStockAt: null,
      leftStockAt: null,
      pipelineRef: null,
      locationId: null,
      valuationRefId: null,
      valuationRefSource: null,
      valuationRefAmount: null,
      valuationRefValuedAt: null,
      isInvoiceable: true,
      version: 1,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
    }
    installFakeBackend([
      { match: new RegExp(`^/inventory/stock-items/${stockId}$`), handler: () => item },
      { match: new RegExp(`^/inventory/stock-items/${stockId}/ledger-entries$`), handler: () => ({ items: [] }) },
    ])

    const { calls, Capture } = captureBreadcrumbs()
    renderWithProviders(
      <Capture>
        <Routes>
          <Route path="/stock/:id" element={<StockDetailPage />} />
        </Routes>
      </Capture>,
      { route: `/stock/${stockId}` },
    )

    await waitFor(() => expect(calls.at(-1)).toContain('VW Golf 2019'))
    const finalSegments = calls.at(-1)!
    expect(finalSegments.some((s) => UUID_LIKE.test(s))).toBe(false)
  })
})
