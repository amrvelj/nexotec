import { describe, expect, it, vi } from 'vitest'
import type { ContactRead } from '../../../api/types'
import type { RowMenuAction } from '@nexotec/ui-kit'
import { buildContactRowMenu, type ContactRowMenuHandlers } from './contactRowMenu'

const t = (key: string, options?: Record<string, unknown>) =>
  options ? `${key}:${JSON.stringify(options)}` : key

function handlers(over: Partial<ContactRowMenuHandlers> = {}): ContactRowMenuHandlers {
  return {
    onNewOffer: vi.fn(),
    onNewContract: vi.fn(),
    onCopyContactNumber: vi.fn(),
    onToggleDoNotContact: vi.fn(),
    onManageCreditBlock: vi.fn(),
    onManageBlock: vi.fn(),
    ...over,
  }
}

type C = Pick<ContactRead, 'lifecycleStatus' | 'creditBlock' | 'creditBlockReason' | 'blocked'>
const cust = (over: Partial<C> = {}): C => ({
  lifecycleStatus: 'active',
  creditBlock: false,
  creditBlockReason: null,
  blocked: false,
  ...over,
})

const createFrom = (menu: ReturnType<typeof buildContactRowMenu>) => menu.overflow.createFrom ?? []
const newContractItem = (menu: ReturnType<typeof buildContactRowMenu>) =>
  createFrom(menu).find((a) => a.label === 'contactRowMenu.newContract')!
const newOfferItem = (menu: ReturnType<typeof buildContactRowMenu>) =>
  createFrom(menu).find((a) => a.label === 'contactRowMenu.newOffer')!

describe('buildContactRowMenu (KAN-44 / ADR-061 anti-drift, FR-21/FR-22)', () => {
  it('a credit-blocked contact may be QUOTED but not CONTRACTED — and the block reason is named', () => {
    const menu = buildContactRowMenu(t, cust({ creditBlock: true, creditBlockReason: 'Overdue invoice 4471' }), handlers())

    expect(newOfferItem(menu).disabled).toBe(false)

    const nc = newContractItem(menu)
    expect(nc.disabled).toBe(true)
    expect(nc.disabledReason).toContain('Overdue invoice 4471')
  })

  it('do-not-contact stops BOTH the offer and the contract', () => {
    const menu = buildContactRowMenu(t, cust({ lifecycleStatus: 'do_not_contact' }), handlers())
    expect(newOfferItem(menu).disabled).toBe(true)
    expect(newOfferItem(menu).disabledReason).toBeTruthy()
    expect(newContractItem(menu).disabled).toBe(true)
  })

  it('KAN-58 — with no flag set, "New contract" is enabled and fires its handler', () => {
    const onNewContract = vi.fn()
    const menu = buildContactRowMenu(t, cust(), handlers({ onNewContract }))
    expect(newOfferItem(menu).disabled).toBe(false)
    const nc = newContractItem(menu)
    expect(nc.disabled).toBe(false)
    expect(nc.disabledReason).toBeUndefined()

    nc.onClick()
    expect(onNewContract).toHaveBeenCalledOnce()
  })

  it('do-not-contact takes precedence over the block for the "New contract" reason', () => {
    const menu = buildContactRowMenu(
      t,
      cust({ lifecycleStatus: 'do_not_contact', creditBlock: true, creditBlockReason: 'x' }),
      handlers(),
    )
    expect(newContractItem(menu).disabledReason).toBe('contactRowMenu.newContractDisabledDoNotContact')
  })

  it('is a pure function — identical input yields an identical enabled state / reason on both surfaces', () => {
    const input = cust({ creditBlock: true, creditBlockReason: 'Overdue' })
    const h = handlers()
    const forHeader = buildContactRowMenu(t, input, h)
    const forGridRow = buildContactRowMenu(t, input, h)
    expect(newContractItem(forHeader).disabled).toBe(newContractItem(forGridRow).disabled)
    expect(newContractItem(forHeader).disabledReason).toBe(newContractItem(forGridRow).disabledReason)
    expect(newOfferItem(forHeader).disabled).toBe(newOfferItem(forGridRow).disabled)
  })

  it('the overflow groups appear in ADR-061 order (navigate/edit/createFrom/exportPrint/destructive)', () => {
    const menu = buildContactRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    const present = (['navigate', 'edit', 'createFrom', 'exportPrint', 'destructive'] as const).filter(
      (g) => (menu.overflow[g]?.length ?? 0) > 0,
    )
    expect(present).toEqual(['edit', 'createFrom', 'exportPrint'])
  })

  it('carries no Delete/anonymise action anywhere (FR-14 not built; merge is the other tombstone)', () => {
    const menu = buildContactRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    const allLabels = [
      menu.primary?.label,
      menu.alternative?.label,
      ...Object.values(menu.overflow).flatMap((g: RowMenuAction[] | undefined) => (g ?? []).map((a) => a.label)),
    ].join(' ')
    expect(/delete|anonymi|löschen|supprim|elimin/i.test(allLabels)).toBe(false)
    expect(menu.overflow.destructive ?? []).toHaveLength(0)
  })

  it('link-vehicle and merge are gated on their handlers (list surface omits both)', () => {
    const listMenu = buildContactRowMenu(t, cust(), handlers())
    expect((listMenu.overflow.edit ?? []).some((a) => a.label === 'contactRowMenu.linkVehicle')).toBe(false)
    expect(createFrom(listMenu).some((a) => a.label === 'contactRowMenu.mergeInto')).toBe(false)

    const detailMenu = buildContactRowMenu(t, cust(), handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }))
    expect((detailMenu.overflow.edit ?? []).some((a) => a.label === 'contactRowMenu.linkVehicle')).toBe(true)
    expect(createFrom(detailMenu).some((a) => a.label === 'contactRowMenu.mergeInto')).toBe(true)
  })

  it('the credit-block entry toggles its label with the current state', () => {
    const setMenu = buildContactRowMenu(t, cust({ creditBlock: false }), handlers())
    expect((setMenu.overflow.edit ?? []).some((a) => a.label === 'contactRowMenu.setCreditBlock')).toBe(true)

    const clearMenu = buildContactRowMenu(t, cust({ creditBlock: true, creditBlockReason: 'x' }), handlers())
    expect((clearMenu.overflow.edit ?? []).some((a) => a.label === 'contactRowMenu.removeCreditBlock')).toBe(true)
  })

  it('KAN-377 — without the sales handlers (a Betrieb without sales) neither sales action is built', () => {
    const detailMenu = buildContactRowMenu(
      t,
      cust(),
      handlers({ onNewOffer: undefined, onNewContract: undefined, onMergeInto: vi.fn(), onLinkVehicle: vi.fn() }),
    )
    expect(createFrom(detailMenu).map((a) => a.label)).toEqual(['contactRowMenu.mergeInto'])
    // KAN-468: without «New offer» the primary is «Link vehicle», with no alternative.
    expect(detailMenu.primary?.label).toBe('contactRowMenu.linkVehicle')
    expect(detailMenu.alternative).toBeUndefined()

    const listMenu = buildContactRowMenu(t, cust(), handlers({ onNewOffer: undefined, onNewContract: undefined }))
    expect(createFrom(listMenu)).toEqual([])
    expect(listMenu.primary).toBeUndefined()
    expect(listMenu.alternative).toBeUndefined()
  })

  describe('KAN-468 (Anto, 2026-10-26; FR-22 amended) — no «Edit»: every field is edited inline', () => {
    const detailHandlers = () => handlers({ onMergeInto: vi.fn(), onLinkVehicle: vi.fn() })
    const allLabels = (menu: ReturnType<typeof buildContactRowMenu>) => [
      menu.primary?.label,
      menu.alternative?.label,
      ...Object.values(menu.overflow).flatMap((g: RowMenuAction[] | undefined) => (g ?? []).map((a) => a.label)),
    ]

    it('the primary is «New offer», with its disabled-with-reason state; the alternative is «Link vehicle»', () => {
      const onNewOffer = vi.fn()
      const menu = buildContactRowMenu(t, cust(), handlers({ onNewOffer, onLinkVehicle: vi.fn() }))
      expect(menu.primary?.label).toBe('contactRowMenu.newOffer')
      expect(menu.alternative?.label).toBe('contactRowMenu.linkVehicle')
      menu.primary?.onClick()
      expect(onNewOffer).toHaveBeenCalledOnce()

      const blocked = buildContactRowMenu(t, cust({ lifecycleStatus: 'do_not_contact' }), detailHandlers())
      expect(blocked.primary?.disabled).toBe(true)
      expect(blocked.primary?.disabledReason).toBe('contactRowMenu.newOfferDisabledDoNotContact')
    })

    it('neither the header nor the overflow, on the detail or the list, offers «Edit»', () => {
      for (const menu of [buildContactRowMenu(t, cust(), detailHandlers()), buildContactRowMenu(t, cust(), handlers())]) {
        expect(allLabels(menu)).not.toContain('contactRowMenu.edit')
      }
    })

    it('the overflow still carries the full row menu: Link vehicle, the flags, the sales actions, merge, copy', () => {
      const menu = buildContactRowMenu(t, cust(), detailHandlers())
      expect((menu.overflow.edit ?? []).map((a) => a.label)).toEqual([
        'contactRowMenu.linkVehicle',
        'contactRowMenu.setDoNotContact',
        'contactRowMenu.setCreditBlock',
        // KAN-810: «Gesperrt», beside the credit block.
        'contactRowMenu.setBlock',
      ])
      expect(createFrom(menu).map((a) => a.label)).toEqual([
        'contactRowMenu.newOffer',
        'contactRowMenu.newContract',
        'contactRowMenu.mergeInto',
      ])
      expect((menu.overflow.exportPrint ?? []).map((a) => a.label)).toEqual(['contactRowMenu.copyContactNumber'])
    })
  })
})
