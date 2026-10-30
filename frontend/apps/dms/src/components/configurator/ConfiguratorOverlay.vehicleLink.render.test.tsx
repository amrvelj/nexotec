// @vitest-environment jsdom
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, PICKED_CODED_VALUES, configurationRead } from '../../test/configuratorFixtures'
import type { ConfigurationRead } from '../../api/types'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'
import { ConfigurationSummaryCard } from './ConfigurationSummaryCard'

// KAN-326 (Anto, 2026-10-12, option A) — a configuration's link to the
// vehicle-mdm record follows its VIN: clearing the VIN clears the link, and
// the API answers `vehicleId: null`, `vehicleLabel: null`.
//
// KAN-340: the configure phase shows the VIN with an edit and a clear
// control, and the vehicle a saved configuration is linked to (its vehicle
// number, `vehicleLabel`) or «Kein Fahrzeug verknüpft» — the server's answer
// after each save. The shared summary card still renders no link (Vehicle 360
// reaches the configuration from the car, through
// `GET /vehicle-mdm/{id}/configuration`, pinned in
// `pages/ConfiguratorHosts.render.test.tsx`).

const LINKED_NO_VIN: Partial<ConfigurationRead> = {
  source: 'manual',
  mode: 'record',
  catalogueVariantId: null,
  catalogueVariantLabel: null,
  ...PICKED_CODED_VALUES,
  vin: null,
  vehicleId: 'veh-1',
  vehicleLabel: 'F-000326',
  mileageKm: 142000,
}

const CLEARED: Partial<ConfigurationRead> = { ...LINKED_NO_VIN, vehicleId: null, vehicleLabel: null, version: 2 }

function install(): { saved: () => Record<string, unknown>[] } {
  const saved: Record<string, unknown>[] = []
  const routes: FakeRoute[] = [
    ...CODED_REFERENCE_ROUTES,
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    // KAN-340: the linked record, read to name it as its own header does.
    { method: 'GET', match: /\/vehicle-mdm\/veh-1$/, handler: () => ({ id: 'veh-1', vehicleNumber: 'F-000326', vin: 'WVWZZZ1KZAW000326' }) },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1$/,
      handler: (req) => {
        saved.push(req.body as Record<string, unknown>)
        // The backend after KAN-326: a VIN cleared clears the link too.
        return configurationRead(CLEARED)
      },
    },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1\/options$/,
      handler: () => configurationRead({ ...CLEARED, version: 3 }),
    },
  ]
  installFakeBackend(routes)
  return { saved: () => saved }
}

/** A host that renders what the overlay committed through the one shared
 * summary card, as the offer, stock and valuation hosts do. */
function Host({ existing }: { existing: ConfigurationRead }) {
  const [committed, setCommitted] = useState<ConfigurationRead | null>(null)
  return (
    <div>
      {committed ? (
        <div data-testid="host">
          <ConfigurationSummaryCard configuration={committed} />
        </div>
      ) : (
        <ConfiguratorOverlay allowedModes={['record']} existing={existing} onCommitted={setCommitted} onClose={vi.fn()} />
      )}
    </div>
  )
}

describe('ConfiguratorOverlay — the vehicle link follows the VIN (KAN-326, KAN-340)', () => {
  it('a save of a configuration linked without a VIN sends vin: null, and the host shows the answer with no vehicle', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host existing={configurationRead(LINKED_NO_VIN)} />)

    // The overlay names the car the stored configuration is linked to.
    await screen.findByLabelText(i18n.t('configurator.summary.mileage'))
    expect(within(screen.getByTestId('configurator-linked-vehicle')).getByText('F-000326')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    // KAN-247: an empty VIN goes out as an explicit null — the clear that,
    // under KAN-326, also drops the link server-side.
    expect(ctx.saved()[0]).toHaveProperty('vin', null)

    const host = await screen.findByTestId('host')
    const card = within(host).getByTestId('configuration-summary-card')
    expect(within(card).queryByText('F-000326')).not.toBeInTheDocument()
    expect(within(card).queryByText(/veh-1/)).not.toBeInTheDocument()
    // The answer the host holds is the backend's: no link.
    expect(within(card).getByText(/142.000 km/)).toBeInTheDocument()
  })

  it('clearing the VIN sends vin: null, and the overlay then shows no vehicle linked', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['record']}
        existing={configurationRead({ ...LINKED_NO_VIN, vin: 'WVWZZZ1KZAW000326' })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const vinField = await screen.findByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })
    expect(vinField).toHaveValue('WVWZZZ1KZAW000326')
    const linked = screen.getByTestId('configurator-linked-vehicle')
    expect(within(linked).getByText('F-000326')).toBeInTheDocument()
    // Named as the vehicle's own detail header names it: number and VIN.
    expect(await within(linked).findByText('WVWZZZ1KZAW000326')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.vehicleLink.clearVin') }))
    expect(vinField).toHaveValue('')
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect(ctx.saved()[0]).toHaveProperty('vin', null)
    await waitFor(() => expect(within(linked).getByText(i18n.t('configurator.vehicleLink.noVehicle'))).toBeInTheDocument())
    expect(within(linked).queryByText('F-000326')).not.toBeInTheDocument()
  })

  it('a link whose record has no label and cannot be read says so, never an id', async () => {
    installFakeBackend([
      ...CODED_REFERENCE_ROUTES,
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'GET',
        match: /\/vehicle-mdm\/veh-gone$/,
        handler: () => status(404, { error: { code: 'not_found', message: 'Vehicle not found.', details: {} } }),
      },
    ])
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['record']}
        existing={configurationRead({ ...LINKED_NO_VIN, vin: 'WVWZZZ1KZAW000326', vehicleId: 'veh-gone', vehicleLabel: null })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const linked = await screen.findByTestId('configurator-linked-vehicle')
    expect(await within(linked).findByText(i18n.t('configurator.vehicleLink.unknownVehicle'))).toBeInTheDocument()
    expect(within(linked).queryByText(/veh-gone/)).not.toBeInTheDocument()
  })

  it('a corrected VIN sends the new value', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['record']}
        existing={configurationRead({ ...LINKED_NO_VIN, vin: 'WVWZZZ1KZAW000326' })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const vinField = await screen.findByRole('textbox', { name: (n) => n.startsWith(i18n.t('configurator.vehicleLink.vin')) })
    await user.clear(vinField)
    await user.type(vinField, 'WVWZZZ1KZAW331338')
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect(ctx.saved()[0]).toHaveProperty('vin', 'WVWZZZ1KZAW331338')
  })

  it('the summary card shows no vehicle link, before or after the clear — it never renders one', () => {
    installFakeBackend([
      ...CODED_REFERENCE_ROUTES,
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])
    const { unmount } = renderWithProviders(
      <ConfigurationSummaryCard configuration={configurationRead({ ...LINKED_NO_VIN, vin: 'WVWZZZ1KZAW000326' })} />,
    )
    const linked = screen.getByTestId('configuration-summary-card')
    expect(within(linked).queryByText('F-000326')).not.toBeInTheDocument()
    expect(within(linked).queryByText(/veh-1/)).not.toBeInTheDocument()
    expect(within(linked).queryByText('WVWZZZ1KZAW000326')).not.toBeInTheDocument()
    unmount()

    renderWithProviders(<ConfigurationSummaryCard configuration={configurationRead(CLEARED)} />)
    const cleared = screen.getByTestId('configuration-summary-card')
    expect(within(cleared).queryByText('F-000326')).not.toBeInTheDocument()
    expect(within(cleared).queryByText(/veh-1/)).not.toBeInTheDocument()
  })
})

// KAN-338 — the API refuses a malformed VIN (422). A configuration saved
// before that rule may hold one: a save that leaves the VIN field untouched
// leaves such a VIN out, so the advisor's work still saves (the field,
// KAN-340, is where it is corrected). A well-formed VIN, and an empty one (KAN-326's clear), still go out.
describe('ConfiguratorOverlay — a stored VIN the API now refuses never blocks a save (KAN-338)', () => {
  it.each([
    ['a malformed stored VIN is left out of the PATCH', 'WVWZZZ1KZAO331338', false],
    ['a well-formed stored VIN is sent as before', 'WVWZZZ1KZAW331338', true],
  ])('%s', async (_name, vin, sent) => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host existing={configurationRead({ ...LINKED_NO_VIN, vin })} />)

    await screen.findByLabelText(i18n.t('configurator.summary.mileage'))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    if (sent) expect(ctx.saved()[0]).toHaveProperty('vin', vin)
    else expect(ctx.saved()[0]).not.toHaveProperty('vin')
  })
})
