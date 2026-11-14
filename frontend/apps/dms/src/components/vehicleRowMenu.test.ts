import { describe, expect, it, vi } from 'vitest'
import type { RowMenuAction } from '@nexotec/ui-kit'
import { buildVehicleRowMenu, type VehicleRowMenuHandlers } from './vehicleRowMenu'

const t = (key: string) => key

function handlers(over: Partial<VehicleRowMenuHandlers> = {}): VehicleRowMenuHandlers {
  return {
    onAllocate: vi.fn(),
    onRecordOdometer: vi.fn(),
    onRecordAccessory: vi.fn(),
    onRecordPlate: vi.fn(),
    onCopyVin: vi.fn(),
    onRequestValuation: vi.fn(),
    onMerge: vi.fn(),
    ...over,
  }
}

const labels = (menu: ReturnType<typeof buildVehicleRowMenu>) => [
  menu.primary.label,
  menu.alternative?.label,
  ...Object.values(menu.overflow).flatMap((g: RowMenuAction[] | undefined) => (g ?? []).map((a) => a.label)),
]

describe('buildVehicleRowMenu (KAN-468; FR-V-16 amended, ADR-061)', () => {
  it('the primary is «Allocate to customer», the alternative «Request valuation»; no «Edit» anywhere', () => {
    const h = handlers()
    const menu = buildVehicleRowMenu(t, h)

    expect(menu.primary.label).toBe('vehicleDetail.header.allocate')
    expect(menu.alternative?.label).toBe('vehicleDetail.header.requestValuation')
    menu.primary.onClick()
    expect(h.onAllocate).toHaveBeenCalledOnce()
    menu.alternative?.onClick()
    expect(h.onRequestValuation).toHaveBeenCalledOnce()
    expect(labels(menu).some((l) => /edit/i.test(l ?? ''))).toBe(false)
  })

  it('the overflow carries FR-V-16 in full: allocate, odometer, accessory, plate, valuation, merge and re-sync', () => {
    const menu = buildVehicleRowMenu(t, handlers())

    expect((menu.overflow.edit ?? []).map((a) => a.label)).toEqual([
      'vehicleDetail.header.allocate',
      'vehicleDetail.header.recordOdometer',
      'vehicleDetail.header.recordAccessory',
      'vehicleDetail.header.recordPlate', // KAN-467
      'vehicleDetail.header.resync',
    ])
    expect((menu.overflow.createFrom ?? []).map((a) => a.label)).toEqual(['vehicleDetail.header.requestValuation'])
    expect((menu.overflow.exportPrint ?? []).map((a) => a.label)).toEqual(['vehicleDetail.header.copyVin'])
    expect((menu.overflow.destructive ?? []).map((a) => a.label)).toEqual(['vehicleDetail.header.merge'])
  })

  it('what is not built yet is shown disabled with its reason, never hidden', () => {
    const menu = buildVehicleRowMenu(t, handlers())
    const all = Object.values(menu.overflow).flatMap((g: RowMenuAction[] | undefined) => g ?? [])
    const resync = all.find((a) => a.label === 'vehicleDetail.header.resync')!
    expect(resync.disabled).toBe(true)
    expect(resync.disabledReason).toBeTruthy()
  })

  it('«Zusammenführen mit…» runs its handler (KAN-524), and without the write right is not offered (KAN-430)', () => {
    const h = handlers()
    const merge = (menu: ReturnType<typeof buildVehicleRowMenu>) =>
      (menu.overflow.destructive ?? []).find((a) => a.label === 'vehicleDetail.header.merge')!

    const allowed = merge(buildVehicleRowMenu(t, h))
    expect(allowed.disabled).toBeFalsy()
    allowed.onClick()
    expect(h.onMerge).toHaveBeenCalledOnce()

    expect(merge(buildVehicleRowMenu(t, handlers({ onMerge: undefined })))).toBeUndefined()
  })

  it('a Betrieb without sales gets no valuation: no alternative, no overflow entry (KAN-198)', () => {
    const menu = buildVehicleRowMenu(t, handlers({ onRequestValuation: undefined }))

    expect(menu.primary.label).toBe('vehicleDetail.header.allocate')
    expect(menu.alternative).toBeUndefined()
    expect(labels(menu)).not.toContain('vehicleDetail.header.requestValuation')
  })
})
