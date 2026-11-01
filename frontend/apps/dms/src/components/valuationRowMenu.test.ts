import { describe, expect, it, vi } from 'vitest'
import { buildValuationRowMenu } from './valuationRowMenu'

const t = (key: string, options?: Record<string, unknown>) => (options ? `${key}:${JSON.stringify(options)}` : key)

describe('buildValuationRowMenu (ADR-061 anti-drift)', () => {
  it('never has a "Bearbeiten" action anywhere — a valuation is immutable once created', () => {
    const menu = buildValuationRowMenu(
      t,
      { status: 'draft', hasSignedContract: true, customerId: null, vehicleId: null },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    )
    const allLabels = [
      menu.primary.label,
      ...(menu.overflow.edit ?? []).map((a) => a.label),
      ...(menu.overflow.navigate ?? []).map((a) => a.label),
    ]
    expect(allLabels.some((l) => l.toLowerCase().includes('bearbeiten') || l.toLowerCase().includes('edit'))).toBe(false)
  })

  it.each([
    // [status, hasSignedContract, expected disabledReason key or null when enabled]
    ['draft', true, null],
    ['valid', true, null],
    ['expired', true, null],
    ['draft', false, 'valuationDetail.actions.markUsedNoSignedContract'],
    ['valid', false, 'valuationDetail.actions.markUsedNoSignedContract'],
    ['expired', false, 'valuationDetail.actions.markUsedNoSignedContract'],
    ['used', true, 'valuationDetail.actions.markUsedAlreadyUsed'],
    ['used', false, 'valuationDetail.actions.markUsedAlreadyUsed'],
  ] as const)(
    'KAN-115: markUsed for status=%s, hasSignedContract=%s is offered only with a signed deal (reason %s)',
    (status, hasSignedContract, expectedReason) => {
      const menu = buildValuationRowMenu(
        t,
        { status, hasSignedContract, customerId: null, vehicleId: null },
        { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
      )
      const markUsed = (menu.overflow.edit ?? [])[0]
      expect(markUsed.disabled).toBe(expectedReason !== null)
      expect(markUsed.disabledReason).toBe(expectedReason ?? undefined)
    }
  )

  it('the SAME disabled/reason pairing is returned regardless of how many times it is called — no per-surface drift', () => {
    // The anti-drift property this function exists to guarantee: calling
    // it twice with identical input (as the detail header and the list
    // grid's row menu each independently do) can never diverge, because
    // both surfaces read from the same, pure computation.
    const input = { status: 'expired' as const, hasSignedContract: false, customerId: null, vehicleId: null }
    const actions = { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    const forHeader = buildValuationRowMenu(t, input, actions)
    const forGridRow = buildValuationRowMenu(t, input, actions)
    expect(forHeader.overflow.edit?.[0].disabled).toBe(forGridRow.overflow.edit?.[0].disabled)
    expect(forHeader.overflow.edit?.[0].disabledReason).toBe(forGridRow.overflow.edit?.[0].disabledReason)
  })

  it('only includes an "open customer"/"open vehicle" navigate entry when the corresponding handler and id are both present', () => {
    const withNeither = buildValuationRowMenu(
      t,
      { status: 'valid', hasSignedContract: false, customerId: null, vehicleId: null },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    )
    expect(withNeither.overflow.navigate ?? []).toHaveLength(0)

    const withBoth = buildValuationRowMenu(
      t,
      { status: 'valid', hasSignedContract: false, customerId: 'c1', vehicleId: 'v1' },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn(), onOpenCustomer: vi.fn(), onOpenVehicle: vi.fn() }
    )
    expect(withBoth.overflow.navigate ?? []).toHaveLength(2)
  })
  it('KAN-253: «Verwendung aufheben» is absent unless the session may write it (onRevertUse passed)', () => {
    const menu = buildValuationRowMenu(
      t,
      { status: 'used', hasSignedContract: false, customerId: null, vehicleId: null },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    )
    expect((menu.overflow.edit ?? []).map((a) => a.label)).toEqual(['valuationDetail.actions.markUsed'])
  })

  it.each([
    // [status, hasSignedContract, expected disabledReason key or null when enabled]
    ['used', false, null],
    ['used', true, 'valuationDetail.actions.revertUseSignedContract'],
    ['valid', false, 'valuationDetail.actions.revertUseNotUsed'],
    ['expired', false, 'valuationDetail.actions.revertUseNotUsed'],
    ['draft', true, 'valuationDetail.actions.revertUseNotUsed'],
  ] as const)(
    'KAN-253: revertUse for status=%s, hasSignedContract=%s is enabled only for a used valuation no signed deal carries (reason %s)',
    (status, hasSignedContract, expectedReason) => {
      const onRevertUse = vi.fn()
      const menu = buildValuationRowMenu(
        t,
        { status, hasSignedContract, customerId: null, vehicleId: null },
        { onRevalue: vi.fn(), onMarkUsed: vi.fn(), onRevertUse }
      )
      const revertUse = (menu.overflow.edit ?? []).find((a) => a.label === 'valuationDetail.actions.revertUse')
      expect(revertUse).toBeDefined()
      expect(revertUse!.disabled).toBe(expectedReason !== null)
      expect(revertUse!.disabledReason).toBe(expectedReason ?? undefined)
      revertUse!.onClick?.()
      expect(onRevertUse).toHaveBeenCalledTimes(1)
    }
  )
})
