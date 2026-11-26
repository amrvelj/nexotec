import { describe, expect, it } from 'vitest'
import type { AreaSettingRead, AreaSettingsRead, SettingDeclarationRead, SettingSlotRead } from '../../api/types'
import { allowedDivisors, numberInForceAt, numberRuleAt, ruleViolation } from './numberRules'

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
    expect(ruleViolation(atA, 40)).toBeNull()
    expect(ruleViolation(atA, 45)).toEqual({ kind: 'notAMultiple', bound: { value: 20, key: GRID.key } })
    // The Betrieb's O7 is in force at loc-b and loc-a (neither holds its own): both grids count.
    const atBetrieb = numberRuleAt(data, slotSetting, null)!
    expect(atBetrieb.multipleOf.map((bound) => bound.value).sort()).toEqual([15, 20])
    expect(ruleViolation(atBetrieb, 45)?.kind).toBe('notAMultiple')
    expect(ruleViolation(atBetrieb, 60)).toBeNull()
  })

  it('O6 must divide 60 and keep O7 a multiple of it; «not set» breaks nothing', () => {
    const data = area([], [slot('45', 'loc-b')])
    const [gridSetting] = data.settings
    const atB = numberRuleAt(data, gridSetting, 'loc-b')!
    expect(ruleViolation(atB, 25)?.kind).toBe('notADivisor')
    expect(ruleViolation(atB, 15)).toBeNull()
    expect(ruleViolation(atB, 30)).toEqual({ kind: 'wouldNotDivide', bound: { value: 45, key: SLOT.key } })
    // loc-a has no O7 and the Betrieb's is not set: only 60 counts there.
    const atA = numberRuleAt(data, gridSetting, 'loc-a')!
    expect(ruleViolation(atA, 30)).toBeNull()
  })

  it('names the other setting while it is in force nowhere in the column', () => {
    const data = area([], [], null)
    const rule = numberRuleAt(data, data.settings[1], null)!
    expect(rule.multipleOf).toEqual([])
    expect(rule.multipleOfNotInForce).toBe(GRID.key)
    expect(ruleViolation(rule, 7)).toBeNull()
  })

  it('a setting with neither rule gets none', () => {
    const plain = declaration('planning.other')
    const data: AreaSettingsRead = { ...area(), settings: [{ declaration: plain, dealership: slot('3'), locations: [] }] }
    expect(numberRuleAt(data, data.settings[0], null)).toBeUndefined()
  })
})
