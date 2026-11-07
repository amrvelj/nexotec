// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, configurationRead, pickCodedFields } from '../test/configuratorFixtures'
import type { CustomerVehicleRead } from '../api/types'
import { ValuationCreatePage } from './ValuationCreatePage'
import { ValuationsListPage } from './ValuationsListPage'
import { SpecificationTab } from '../components/vehicle-detail/SpecificationTab'
import { VehiclesTab } from '../components/customer-detail/VehiclesTab'
import { nameValuationCar } from '../test/nameValuationCar'

// C-F (KAN-10) — the valuation host (FR-C-14, record only), Vehicle 360's
// read-only Specification tab (FR-C-15), and Customer 360 reaching it only
// through the vehicle.

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

const CATALOGUE: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  ...CODED_REFERENCE_ROUTES,
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
  { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
]

describe('Valuation → new valuation (FR-C-14)', () => {
  it('captures the car in record mode first, then values it with the configuration', async () => {
    const user = userEvent.setup()
    const posted: Record<string, unknown>[] = []
    const captured = configurationRead({
      id: 'cfg-v', mode: 'record', source: 'manual', catalogueVariantId: null, brandDisplayName: 'Subaru',
      modelGroupName: 'Justy', variantName: 'G3X', licencePlate: 'ZH123456', mileageKm: 148000,
      firstRegistrationDate: '2003-10-03',
    })
    installFakeBackend([
      { method: 'POST', match: /^\/configurations$/, handler: () => ({ __status: 201, body: captured }) },
      {
        method: 'POST',
        match: /^\/valuations$/,
        handler: (req) => {
          posted.push(req.body as Record<string, unknown>)
          return { __status: 201, body: { id: 'val-1' } }
        },
      },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <Routes>
        <Route path="/valuations/new" element={<ValuationCreatePage />} />
        <Route path="/valuations/:id" element={<div>valuation detail</div>} />
      </Routes>,
      { route: '/valuations/new' },
    )

    await screen.findByText(i18n.t('configurator.find.title'))
    // Record only: no mode switch, no build mode.
    expect(screen.queryByText(i18n.t('configurator.mode.build'))).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await pickCodedFields()
    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.saveNew') }))

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(within(dialog).getByTestId('configuration-summary-card')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.make'), { exact: false })).toHaveValue('Subaru')
    await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false }), '2500')
    await nameValuationCar(user, dialog)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      configurationId: 'cfg-v', vehicleMake: 'Subaru', vehiclePlate: 'ZH123456', mileage: 148000,
      vehicleFirstRegistration: '2003-10-03',
    })
    expect(await screen.findByText('valuation detail')).toBeInTheDocument()
  })

  it('KAN-468: requested from a vehicle, the capture starts from its VIN and the dialog takes its facts', async () => {
    const user = userEvent.setup()
    const vehicle = {
      id: 'veh-1', vin: 'WVWZZZ1JZXW000001', vehicleNumber: 'F-000001', stammnummer: null, typeApprovalNumber: null,
      catalogueVariantId: 'var-1', mergedIntoVehicleId: null, catalogueMatchStatus: 'matched', vehicleStatus: 'active',
      firstRegistrationDate: '1999-04-01', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    }
    installFakeBackend([
      { method: 'GET', match: /^\/vehicle-mdm\/veh-1$/, handler: () => vehicle },
      {
        method: 'GET',
        match: /^\/catalogue\/variants\/var-1$/,
        handler: () => ({ id: 'var-1', brandDisplayName: 'Volkswagen', modelGroupName: 'Golf', variantName: '1.6 Comfortline' }),
      },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <Routes>
        <Route path="/valuations/new" element={<ValuationCreatePage />} />
        <Route path="/vehicles/:id" element={<div>vehicle detail</div>} />
      </Routes>,
      { route: '/valuations/new?vehicle=veh-1' },
    )

    // Record only, and the identification input already holds the car's VIN.
    await screen.findByText(i18n.t('configurator.find.title'))
    expect(screen.queryByText(i18n.t('configurator.mode.build'))).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.find.idLabel')) })).toHaveValue(
      vehicle.vin,
    )

    // Skipping the capture: the dialog still has the VIN and first registration.
    await user.click(screen.getByRole('button', { name: i18n.t('valuationCreate.configurator.skip') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'), { exact: false })).toHaveValue(vehicle.vin)
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.firstRegistration'))).toHaveValue('1999-04-01')
    // Make, model and trim come from the catalogue variant the car is matched to.
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.make'), { exact: false })).toHaveValue('Volkswagen')
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.model'), { exact: false })).toHaveValue('Golf')
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.trim'))).toHaveValue('1.6 Comfortline')

    // Closing returns to the vehicle it was requested from.
    await user.click(within(dialog).getByRole('button', { name: i18n.t('common.cancel') }))
    expect(await screen.findByText('vehicle detail')).toBeInTheDocument()
  })

  it('opens from the Valuations list as an overlay over the list, record only', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'POST',
        match: /^\/configurations$/,
        handler: () => ({
          __status: 201,
          body: configurationRead({ id: 'cfg-l', mode: 'record', source: 'manual', catalogueVariantId: null, brandDisplayName: 'Subaru' }),
        }),
      },
      { method: 'GET', match: /^\/valuations$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
      ...CATALOGUE,
    ])
    renderWithProviders(
      <Routes>
        <Route path="/valuations" element={<ValuationsListPage />} />
        <Route path="/valuations/new" element={<ValuationCreatePage />} />
      </Routes>,
      { route: '/valuations' },
    )

    await user.click((await screen.findAllByRole('button', { name: i18n.t('valuationsList.newValuation') }))[0])
    const overlay = await screen.findByRole('dialog')
    // An overlay over the list: the list is still mounted underneath.
    expect(screen.getByRole('heading', { name: i18n.t('valuationsList.title') })).toBeInTheDocument()
    expect(within(overlay).queryByText(i18n.t('configurator.mode.build'))).not.toBeInTheDocument()
    await user.click(within(overlay).getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await pickCodedFields()
    await user.click(await within(overlay).findByRole('button', { name: i18n.t('configurator.saveNew') }))

    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(within(dialog).getByLabelText(i18n.t('valuationCreate.make'), { exact: false })).toHaveValue('Subaru')
    expect(within(dialog).getByTestId('configuration-summary-card')).toBeInTheDocument()
  })

  it('still lets the advisor enter a car by hand, without a configuration', async () => {
    const user = userEvent.setup()
    installFakeBackend(CATALOGUE)
    renderWithProviders(
      <Routes>
        <Route path="/valuations/new" element={<ValuationCreatePage />} />
      </Routes>,
      { route: '/valuations/new' },
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('valuationCreate.configurator.skip') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
    expect(within(dialog).queryByTestId('configuration-summary-card')).not.toBeInTheDocument()
  })
})

describe('Vehicle 360 → Specification (FR-C-15)', () => {
  it("renders this dealership's configuration read-only through the summary card", async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/vehicle-mdm\/veh-1\/configuration$/, handler: () => configurationRead() },
    ])
    renderWithProviders(<SpecificationTab vehicleId="veh-1" />)

    const card = await screen.findByTestId('configuration-summary-card')
    // Read-only: no way into the configurator from Vehicle 360.
    expect(within(card).queryByText(i18n.t('configurator.summary.open'))).not.toBeInTheDocument()
  })

  it('says plainly that the dealership holds no configuration of the car', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/vehicle-mdm\/veh-1\/configuration$/,
        handler: () => status(404, { error: { code: 'not_found', message: 'x' } }),
      },
    ])
    renderWithProviders(<SpecificationTab vehicleId="veh-1" />)

    expect(await screen.findByText(i18n.t('vehicleDetail.specification.empty'))).toBeInTheDocument()
  })
})

describe('Customer 360 reaches a configuration only through the vehicle (FR-C-15)', () => {
  it("links each of the customer's vehicles to that vehicle's Specification tab", async () => {
    installFakeBackend([])
    const row = {
      id: 'party-1', customerId: 'cust-1', vehicleId: 'veh-1', role: 'owner', effectiveFrom: '2024-01-01',
      effectiveTo: null, createdAt: '2024-01-01T00:00:00Z', updatedAt: '2024-01-01T00:00:00Z', otherParties: [],
      stockItem: null,
      vehicle: { id: 'veh-1', vin: 'WVWZZZ1KZAW000001', vehicleNumber: 'F-000001', make: null, model: null, modelYear: null },
    } as unknown as CustomerVehicleRead
    renderWithProviders(<VehiclesTab vehicles={[row]} loading={false} error={null} locale="de-CH" onAdd={vi.fn()} />)

    const link = await screen.findByRole('link', { name: i18n.t('customerDetail.vehicles.specification') })
    expect(link).toHaveAttribute('href', '/vehicles/veh-1?tab=specification')
  })
})
