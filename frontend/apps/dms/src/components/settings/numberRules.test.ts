import { describe, expect, it } from 'vitest'
import type { AreaSettingRead, AreaSettingsRead, SettingDeclarationRead, SettingSlotRead } from '../../api/types'
import { allowedDivisors, declarationDivisorViolation, multipleRuleViolation, numberInForceAt, numberRuleAt } from './numberRules'

// KAN-808 (PR-1c review finding 5): the screen's mirror of the store's `divides` and
// `multipleOf` (setting_declarations.validate_value, setting_rules) — O6 a divisor of 60,
// O7 a multiple of O6 wherever both are in force.

const declaration = (key: string, extra: Partial<SettingDeclarationRead> = {}): SettingDeclarationRead => ({
  key,
  area: 'planning',
  module: 'aftersales',
  kind: 'number',
  whenNotSet: 'refuses',
  actsAs: null,
  minimum: '5',
  maximum: null,
  decimals: 0,
  unit: 'minutes',
  choices: [],
  checklistItem: true,
  checklistBlocks: null,
  choiceRequires: [],
  divides: null,
  multipleOf: null,
  ...extra,
})

const slot = (number: string | null, locationId: string | null = null): SettingSlotRead => ({
  locationId,
  isSet: number !== null,
  value: number === null ? null : { number },
  version: number === null ? 0 : 1,
  changedAt: null,
  changedBy: null,
  fitsDeclaration: true,
})

const GRID = declaration('planning.grid_minutes', { maximum: '60', divides: '60' })
const SLOT = declaration('planning.slot_minutes', { maximum: '480', multipleOf: 'planning.grid_minutes' })

function area(grid: AreaSettingRead['locations'] = [], slots: AreaSettingRead['locations'] = [], gridValue: string | null = '15', slotValue: string | null = null): AreaSettingsRead {
  return {
    area: 'planning',
    showsLocations: true,
    locations: [
      { id: 'loc-a', name: 'Hauptsitz', isActive: true },
      { id: 'loc-b', name: 'Filiale', isActive: true },
    ],
    settings: [
      { declaration: GRID, dealership: slot(gridValue), locations: grid },
      { declaration: SLOT, dealership: slot(slotValue), locations: slots },
    ],
  }
}

describe('the settings screen’s number rules (KAN-808)', () => {
  it('a divides rule offers the divisors within the bounds — for O6, of 60 from 5 to 60', () => {
    expect(allowedDivisors(GRID)).toEqual([5, 6, 10, 12, 15, 20, 30, 60])
    expect(allowedDivisors(SLOT)).toBeNull()
  })

  it('the value in force is the location’s own, else the Betrieb’s', () => {
    const data = area([slot('20', 'loc-a')])
    expect(numberInForceAt(data, GRID.key, 'loc-a')).toBe(20)
    expect(numberInForceAt(data, GRID.key, 'loc-b')).toBe(15)
    expect(numberInForceAt(data, GRID.key, null)).toBe(15)
  })

  it('O7 is checked against O6 in force in its column', () => {
    const data = area([slot('20', 'loc-a')])
    const [, slotSetting] = data.settings
    const atA = numberRuleAt(data, slotSetting, 'loc-a')!
    expect(atA.multipleOf).toEqual([{ value: 20, key: GRID.key }])
    expect(multipleRuleViolation(data, SLOT.key, 'loc-a', 40)).toBeNull()
    expect(multipleRuleViolation(data, SLOT.key, 'loc-a', 45)).toEqual({ kind: 'notAMultiple', bound: { value: 20, key: GRID.key }, value: 45 })
    // The Betrieb's O7 is in force at loc-b and loc-a (neither holds its own): both grids count.
    const atBetrieb = numberRuleAt(data, slotSetting, null)!
    expect(atBetrieb.multipleOf.map((bound) => bound.value).sort()).toEqual([15, 20])
    expect(multipleRuleViolation(data, SLOT.key, null, 45)?.kind).toBe('notAMultiple')
    expect(multipleRuleViolation(data, SLOT.key, null, 60)).toBeNull()
  })

  it('O6 must divide 60 and keep O7 a multiple of it; «not set» breaks nothing', () => {
    const data = area([], [slot('45', 'loc-b')])
    const [gridSetting] = data.settings
    const atB = numberRuleAt(data, gridSetting, 'loc-b')!
    expect(declarationDivisorViolation(atB, 25)?.kind).toBe('notADivisor')
    expect(declarationDivisorViolation(atB, 15)).toBeNull()
    expect(multipleRuleViolation(data, GRID.key, 'loc-b', 15)).toBeNull()
    expect(multipleRuleViolation(data, GRID.key, 'loc-b', 30)).toEqual({ kind: 'wouldNotDivide', bound: { value: 45, key: SLOT.key }, value: 30 })
    // loc-a has no O7 and the Betrieb's is not set: only 60 counts there.
    const atA = numberRuleAt(data, gridSetting, 'loc-a')!
    expect(declarationDivisorViolation(atA, 30)).toBeNull()
    expect(multipleRuleViolation(data, GRID.key, 'loc-a', 30)).toBeNull()
  })

  it('names the other setting while it is in force nowhere in the column', () => {
    const data = area([], [], null)
    const rule = numberRuleAt(data, data.settings[1], null)!
    expect(rule.multipleOf).toEqual([])
    expect(rule.multipleOfNotInForce).toBe(GRID.key)
    expect(multipleRuleViolation(data, SLOT.key, null, 7)).toBeNull()
  })

  it('a setting with neither rule gets none', () => {
    const plain = declaration('planning.other')
    const data: AreaSettingsRead = { ...area(), settings: [{ declaration: plain, dealership: slot('3'), locations: [] }] }
    expect(numberRuleAt(data, data.settings[0], null)).toBeUndefined()
  })
})

describe('the screen refuses exactly what check_multiple_rules refuses (KAN-842)', () => {
  it('only a scope the write newly breaks refuses: a reactivated location already broken does not block the Betrieb', () => {
    // loc-a holds its own O6 20 under the Betrieb's O7 45: already broken there. 45 → 75 leaves it broken, never worse.
    const data = area([slot('20', 'loc-a')], [], '15', '45')
    expect(multipleRuleViolation(data, SLOT.key, null, 75)).toBeNull()
    // 45 → 50 newly breaks the Betrieb's own scope (O6 15).
    expect(multipleRuleViolation(data, SLOT.key, null, 50)).toEqual({
      kind: 'notAMultiple', bound: { value: 15, key: GRID.key }, value: 50,
    })
  })

  it('a stale location value reads as not set: the Betrieb’s value is in force there', () => {
    const stale = { ...slot('20', 'loc-a'), fitsDeclaration: false }
    const data = area([stale], [], '15', '45')
    // At loc-a O6 is the Betrieb's 15 (the stale 20 does not count), so 45 stays a multiple there and 75 too.
    expect(multipleRuleViolation(data, SLOT.key, null, 75)).toBeNull()
    expect(multipleRuleViolation(data, GRID.key, null, 20)).toEqual({
      kind: 'wouldNotDivide', bound: { value: 45, key: SLOT.key }, value: 20,
    })
  })

  it('a clear is checked: clearing a location’s O7 leaves the Betrieb’s value in force there', () => {
    // loc-a: own O6 20 and own O7 40. Clearing its O7 leaves the Betrieb's 45 at 20 — newly broken.
    const data = area([slot('20', 'loc-a')], [slot('40', 'loc-a')], '15', '45')
    expect(multipleRuleViolation(data, SLOT.key, 'loc-a', null)).toEqual({
      kind: 'notAMultiple', bound: { value: 20, key: GRID.key }, value: 45,
    })
    // Clearing loc-a's O6 instead leaves the Betrieb's 15 there, which does not divide its 40.
    expect(multipleRuleViolation(data, GRID.key, 'loc-a', null)).toEqual({
      kind: 'wouldNotDivide', bound: { value: 40, key: SLOT.key }, value: 15,
    })
    // With loc-a's O7 at 60 the same clear is fine (15 divides 60).
    expect(multipleRuleViolation(area([slot('20', 'loc-a')], [slot('60', 'loc-a')], '15', '45'), GRID.key, 'loc-a', null)).toBeNull()
  })

  it('«not set» on either side breaks nothing, and a key without a multiple rule is never refused', () => {
    expect(multipleRuleViolation(area([], [], null, '45'), SLOT.key, null, 47)).toBeNull()
    const lone = { ...area(), settings: [area().settings[0]] }
    expect(multipleRuleViolation(lone, GRID.key, null, 20)).toBeNull()
  })
})
