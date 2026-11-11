// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { ApiError } from '../../api/client'
import { VehicleChangedBanner } from './VehicleChangedBanner'

// KAN-553 (ADR-115): the banner names what Stock changed, in the banner's
// own order and the UI language; the actions run the page's handlers and
// wait for a save in flight; a sent offer shows it without actions.

describe('VehicleChangedBanner (KAN-553)', () => {
  beforeEach(() => {
    installFakeBackend([])
  })

  it('is absent without a flag', () => {
    renderWithProviders(<VehicleChangedBanner vehicleChangedAt={null} vehicleChangedFields={null} />)
    expect(screen.queryByTestId('vehicle-changed-banner')).toBeNull()
  })

  it('names the changed fields and runs refresh and dismiss', async () => {
    const user = userEvent.setup()
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const onDismiss = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(
      <VehicleChangedBanner
        vehicleChangedAt="2026-11-11T10:00:00Z"
        vehicleChangedFields={['exteriorColour', 'listPrice']}
        onRefresh={onRefresh}
        onDismiss={onDismiss}
      />,
    )
    expect(screen.getByText('Fahrzeug im Lager geändert')).toBeInTheDocument()
    expect(screen.getByText('Seit dieser Preisberechnung geändert: Listenpreis, Aussenfarbe.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Aktualisieren' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Ignorieren' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('waits while a save is in flight', () => {
    renderWithProviders(
      <VehicleChangedBanner vehicleChangedAt="2026-11-11T10:00:00Z" vehicleChangedFields={['listPrice']} onRefresh={vi.fn()} onDismiss={vi.fn()} busy />,
    )
    expect(screen.getByRole('button', { name: 'Aktualisieren' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Ignorieren' })).toBeDisabled()
  })

  it('says a refusal in the UI language', async () => {
    const user = userEvent.setup()
    const onRefresh = vi.fn().mockRejectedValue(new ApiError(409, 'conflict', 'The stock vehicle has not changed.', { reason: 'vehicle_not_changed' }))
    renderWithProviders(
      <VehicleChangedBanner vehicleChangedAt="2026-11-11T10:00:00Z" vehicleChangedFields={['listPrice']} onRefresh={onRefresh} onDismiss={vi.fn()} />,
    )
    await user.click(screen.getByRole('button', { name: 'Aktualisieren' }))
    expect(await screen.findByText('Das Lagerfahrzeug wurde seit der Preisberechnung nicht geändert.')).toBeInTheDocument()
  })

  it('a sent offer shows it without actions', () => {
    renderWithProviders(<VehicleChangedBanner vehicleChangedAt="2026-11-11T10:00:00Z" vehicleChangedFields={['odometerKm']} />)
    expect(screen.getByText('Seit dieser Preisberechnung geändert: Kilometerstand.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText(/Die Offerte ist versendet/)).toBeInTheDocument()
  })
})
