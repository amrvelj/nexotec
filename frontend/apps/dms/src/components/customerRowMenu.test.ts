import { describe, expect, it, vi } from 'vitest'
import type { CustomerRead } from '../api/types'
import type { RowMenuAction } from '@nexotec/ui-kit'
import { buildCustomerRowMenu, type CustomerRowMenuHandlers } from './customerRowMenu'

const t = (key: string, options?: Record<string, unknown>) =>
  options ? `${key}:${JSON.stringify(options)}` : key

function handlers(over: Partial<CustomerRowMenuHandlers> = {}): CustomerRowMenuHandlers {
  return {
    onEdit: vi.fn(),
    onNewOffer: vi.fn(),
    onNewContract: vi.fn(),
    onCopyCustomerNumber: vi.fn(),
    onToggleDoNotContact: vi.fn(),
    onManageCreditBlock: vi.fn(),
    ...over,
  }
}

type C = Pick<CustomerRead, 'lifecycleStatus' | 'creditBlock' | 'creditBlockReason'>
const cust = (over: Partial<C> = {}): C => ({
  lifecycleStatus: 'active',
  creditBlock: false,
  creditBlockReason: null,
  ...over,
})

const createFrom = (menu: ReturnType<typeof buildCustomerRowMenu>) => menu.overflow.createFrom ?? []
const newContractItem = (menu: ReturnType<typeof buildCustomerRowMenu>) =>
  createFrom(menu).find((a) => a.label === 'customerRowMenu.newContract')!
const newOfferItem = (menu: ReturnType<typeof buildCustomerRowMenu>) =>
  createFrom(menu).find((a) => a.label === 'customerRowMenu.newOffer')!

describe('buildCustomerRowMenu (KAN-44 / ADR-061 anti-drift, FR-21/FR-22)', () => {
  it('a credit-blocked customer may be QUOTED but not CONTRACTED — and the block reason is named', () => {
    const menu = buildCustomerRowMenu(t, cust({ creditBlock: true, creditBlockReason: 'Overdue invoice 4471' }), handlers())

    expect(newOfferItem(menu).disabled).toBe(false)

    const nc = newContractItem(menu)
    expect(nc.disabled).toBe(true)
    expect(nc.disabledReason).toContain('Overdue invoice 4471')
  })

  it('do-not-contact stops BOTH the offer and the contract', () => {
    const menu = buildCustomerRowMenu(t, cust({ lifecycleStatus: 'do_not_contact' }), handlers())
    expect(newOfferItem(menu).disabled).toBe(true)
    expect(newOfferItem(menu).disabledReason).toBeTruthy()
    expect(newContractItem(menu).disabled).toBe(true)
  })

  it('KAN-58 — with no flag set, "New contract" is enabled and fires its handler', () => {
    const onNewContract = vi.fn()
    const menu = buildCustomerRowMenu(t, cust(), handlers({ onNewContract }))
    expect(newOfferItem(menu).disabled).toBe(false)
    const nc = newContractItem(menu)
    expect(nc.disabled).toBe(false)
    expect(nc.disabledReason).toBeUndefined()

    nc.onClick()
    expect(onNewContract).toHaveBeenCalledOnce()
  })

  it('do-not-contact takes precedence over the block for the "New contract" reason', () => {
    const menu = buildCustomerRowMenu(
      t,
      cust({ lifecycleStatus: 'do_not_contact', creditBlock: true, creditBlockReason: 'x' }),
      handlers(),
    )
    expect(newContractItem(menu).disabledReason).toBe('customerRowMenu.newContractDisabledDoNotContact')
  })

  it('is a pure function — identical input yields an identical enabled state / reason on both surfaces', () => {
    const input = cust({ creditBlock: true, creditBlockReason: 'Overdue' })
    const h = handlers()
    const forHeader = buildCustomerRowMenu(t, input, h)
    const forGridRow = buildCustomerRowMenu(t, input, h)
    expect(newContractItem(forHeader).disabled).toBe(newContractItem(forGridRow).disabled)
    expect(newContractItem(forHeader).disabledReason).toBe(newContractItem(forGridRow).disabledReason)
    expect(newOfferItem(forHeader).disabled).toBe(newOfferItem(forGridRow).disabled)
  })

  it('the overflow groups appear in ADR-061 order (navigate/edit/createFrom/exportPrint/destructive)', () => {
    const menu = buildCustomerRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    const present = (['navigate', 'edit', 'createFrom', 'exportPrint', 'destructive'] as const).filter(
      (g) => (menu.overflow[g]?.length ?? 0) > 0,
    )
    expect(present).toEqual(['edit', 'createFrom', 'exportPrint'])
  })

  it('carries no Delete/anonymise action anywhere (FR-14 not built; merge is the other tombstone)', () => {
    const menu = buildCustomerRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    const allLabels = [
      menu.primary.label,
      menu.alternative.label,
      ...Object.values(menu.overflow).flatMap((g: RowMenuAction[] | undefined) => (g ?? []).map((a) => a.label)),
    ].join(' ')
    expect(/delete|anonymi|löschen|supprim|elimin/i.test(allLabels)).toBe(false)
    expect(menu.overflow.destructive ?? []).toHaveLength(0)
  })

  it('link-vehicle and merge are gated on their handlers (list surface omits both)', () => {
    const listMenu = buildCustomerRowMenu(t, cust(), handlers())
    expect((listMenu.overflow.edit ?? []).some((a) => a.label === 'customerRowMenu.linkVehicle')).toBe(false)
    expect(createFrom(listMenu).some((a) => a.label === 'customerRowMenu.mergeInto')).toBe(false)

    const detailMenu = buildCustomerRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    expect((detailMenu.overflow.edit ?? []).some((a) => a.label === 'customerRowMenu.linkVehicle')).toBe(true)
    expect(createFrom(detailMenu).some((a) => a.label === 'customerRowMenu.mergeInto')).toBe(true)
  })

  it('the credit-block entry toggles its label with the current state', () => {
    const setMenu = buildCustomerRowMenu(t, cust({ creditBlock: false }), handlers())
    expect((setMenu.overflow.edit ?? []).some((a) => a.label === 'customerRowMenu.setCreditBlock')).toBe(true)

    const clearMenu = buildCustomerRowMenu(t, cust({ creditBlock: true, creditBlockReason: 'x' }), handlers())
    expect((clearMenu.overflow.edit ?? []).some((a) => a.label === 'customerRowMenu.removeCreditBlock')).toBe(true)
  })
})
