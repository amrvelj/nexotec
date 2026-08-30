import { describe, expect, it, vi } from 'vitest'
import { buildValuationRowMenu } from './valuationRowMenu'

const t = (key: string, options?: Record<string, unknown>) => (options ? `${key}:${JSON.stringify(options)}` : key)

describe('buildValuationRowMenu (ADR-061 anti-drift)', () => {
  it('never has a "Bearbeiten" action anywhere — a valuation is immutable once created', () => {
    const menu = buildValuationRowMenu(
      t,
      { status: 'draft', customerId: null, vehicleId: null },
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
    ['draft', false],
    ['valid', false],
    ['expired', true],
    ['used', true],
  ] as const)('markUsed is disabled=%s -> %s, with a reason whenever disabled', (status, expectedDisabled) => {
    const menu = buildValuationRowMenu(
      t,
      { status, customerId: null, vehicleId: null },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    )
    const markUsed = (menu.overflow.edit ?? [])[0]
    expect(markUsed.disabled).toBe(expectedDisabled)
    if (expectedDisabled) {
      expect(markUsed.disabledReason).toBeTruthy()
    } else {
      expect(markUsed.disabledReason).toBeUndefined()
    }
  })

  it('the SAME disabled/reason pairing is returned regardless of how many times it is called — no per-surface drift', () => {
    // The anti-drift property this function exists to guarantee: calling
    // it twice with identical input (as the detail header and the list
    // grid's row menu each independently do) can never diverge, because
    // both surfaces read from the same, pure computation.
    const input = { status: 'expired' as const, customerId: null, vehicleId: null }
    const actions = { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    const forHeader = buildValuationRowMenu(t, input, actions)
    const forGridRow = buildValuationRowMenu(t, input, actions)
    expect(forHeader.overflow.edit?.[0].disabled).toBe(forGridRow.overflow.edit?.[0].disabled)
    expect(forHeader.overflow.edit?.[0].disabledReason).toBe(forGridRow.overflow.edit?.[0].disabledReason)
  })

  it('only includes an "open customer"/"open vehicle" navigate entry when the corresponding handler and id are both present', () => {
    const withNeither = buildValuationRowMenu(
      t,
      { status: 'valid', customerId: null, vehicleId: null },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn() }
    )
    expect(withNeither.overflow.navigate ?? []).toHaveLength(0)

    const withBoth = buildValuationRowMenu(
      t,
      { status: 'valid', customerId: 'c1', vehicleId: 'v1' },
      { onRevalue: vi.fn(), onMarkUsed: vi.fn(), onOpenCustomer: vi.fn(), onOpenVehicle: vi.fn() }
    )
    expect(withBoth.overflow.navigate ?? []).toHaveLength(2)
  })
})
