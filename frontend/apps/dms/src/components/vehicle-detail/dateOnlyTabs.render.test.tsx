// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import { AccessoriesTab } from './AccessoriesTab'
import { OdometerTab } from './OdometerTab'
import { PlatesTab } from './PlatesTab'
import type { VehicleAccessoryRead, VehicleOdometerReadingRead, VehiclePlateRead } from '../../api/types'

// KAN-333 — a plate's and an accessory's validity and an odometer reading's
// date are date-only values. They rendered as raw ISO (`2026-03-05`, not
// FR-13's dd.MM.yyyy); through formatDateOnly they read as the recorded day
// in every zone, here one west of Greenwich.
describe('Vehicle detail tabs — date-only values read dd.MM.yyyy west of Greenwich (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  const wrap = (ui: React.ReactNode) => render(<MantineProvider theme={theme} env="test">{ui}</MantineProvider>)

  it('Plates: valid from and valid to', () => {
    const plates = [
      { id: 'p1', plate: 'ZH 123456', canton: 'ZH', validFrom: '2021-04-01', validTo: '2024-06-30', isInterchangeable: false },
    ] as VehiclePlateRead[]
    wrap(<PlatesTab plates={plates} loading={false} />)
    expect(screen.getByText('01.04.2021')).toBeInTheDocument()
    expect(screen.getByText('30.06.2024')).toBeInTheDocument()
    expect(screen.queryByText('2021-04-01')).not.toBeInTheDocument()
  })

  it('Odometer: the reading date', () => {
    const readings = [
      { id: 'r1', value: 12500, readingDate: '2026-03-05', source: 'manual', implausible: false },
    ] as VehicleOdometerReadingRead[]
    wrap(<OdometerTab readings={readings} loading={false} onAdd={vi.fn()} />)
    expect(screen.getByText('05.03.2026')).toBeInTheDocument()
    expect(screen.queryByText('04.03.2026')).not.toBeInTheDocument()
  })

  it('Accessories: valid from and valid to', () => {
    const accessories = [
      { id: 'a1', accessoryType: 'Anhängerkupplung', description: null, validFrom: '2022-01-01', validTo: '2025-12-31' },
    ] as VehicleAccessoryRead[]
    wrap(<AccessoriesTab accessories={accessories} loading={false} onAdd={vi.fn()} onRemove={vi.fn()} />)
    expect(screen.getByText('01.01.2022')).toBeInTheDocument()
    expect(screen.getByText('31.12.2025')).toBeInTheDocument()
    expect(screen.queryByText('31.12.2021')).not.toBeInTheDocument()
  })
})
