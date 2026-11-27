import type { AreaSettingRead, AreaSettingsRead, SettingDeclarationRead } from '../../api/types'

/**
 * KAN-808 (PR-1c review finding 5) — the settings store's two number rules,
 * as the area API names them on a declaration:
 * - `divides: n` — the value divides `n` without remainder (O6, the planner's
 *   grid: a divisor of 60);
 * - `multipleOf: key` — the value is a whole multiple of that key's value
 *   wherever both are in force (O7, the slot length, of O6); and so the other
 *   key, written, must still divide it.
 * The screen mirrors them before the write (KAN-842: `multipleRuleViolation`
 * is `setting_rules.check_multiple_rules` line for line — only a scope the
 * write newly breaks refuses, a stale slot reads as not set, a clear is
 * checked too), as it mirrors check 49's
 * `choiceRequires` (SettingRows' `unavailableAt`): in a column, the other
 * key's value in force is the location's own, else the Betrieb's, else what
 * «not set» acts as; «not set» on either side breaks nothing
 * (`setting_rules.py`). The API stays the authority: a key of another area is
 * not in the area's data, so there the refusal shows.
 */

/** A number the value is checked against; `key` names the setting it comes from
 * (null: the declaration's own `divides`). */
export interface RuleBound {
  value: number
  key: string | null
}

export interface NumberRule {
  /** Numbers the value must divide without remainder. */
  divides: RuleBound[]
  /** Numbers the value must be a whole multiple of. */
  multipleOf: RuleBound[]
  /** The declaration's `multipleOf` key while it is in force nowhere in this column:
   * nothing to check yet, but the hint still names it. */
  multipleOfNotInForce: string | null
}

/** `value`: the number the refusal is about when it is not the one typed — a clear leaves
 * another value in force (KAN-842). */
export type RuleViolation =
  | { kind: 'notADivisor'; bound: RuleBound; value?: number }
  | { kind: 'notAMultiple'; bound: RuleBound; value?: number }
  | { kind: 'wouldNotDivide'; bound: RuleBound; value?: number }

const whole = (value: number) => Number.isInteger(value) && value > 0

/** The first rule `value` breaks, or null. */
export function ruleViolation(rule: NumberRule, value: number): RuleViolation | null {
  if (!whole(value)) return null // the declaration's own refusal (decimals, minimum) says why
  for (const bound of rule.divides) {
    if (whole(bound.value) && bound.value % value !== 0) {
      return { kind: bound.key === null ? 'notADivisor' : 'wouldNotDivide', bound }
    }
  }
  for (const bound of rule.multipleOf) {
    if (whole(bound.value) && value % bound.value !== 0) return { kind: 'notAMultiple', bound }
  }
  return null
}

/** The values a `divides` declaration takes: the divisors of its number within its
 * bounds, ascending; null for a declaration without the rule. */
export function allowedDivisors(declaration: SettingDeclarationRead): number[] | null {
  if (declaration.divides == null || declaration.decimals !== 0) return null
  const n = Number(declaration.divides)
  if (!whole(n)) return null
  const minimum = declaration.minimum != null ? Number(declaration.minimum) : 1
  const maximum = declaration.maximum != null ? Number(declaration.maximum) : n
  const divisors: number[] = []
  for (let d = 1; d <= n; d += 1) if (n % d === 0 && d >= minimum && d <= maximum) divisors.push(d)
  return divisors
}

/** The number in force for `key` at a location (null: the Betrieb's column): the
 * location's own value, else the Betrieb's, else what «not set» acts as. */
export function numberInForceAt(data: AreaSettingsRead, key: string, locationId: string | null): number | null {
  const setting = data.settings.find((s) => s.declaration.key === key)
  if (!setting) return null
  const own =
    locationId === null
      ? null
      : setting.locations.find((slot) => slot.locationId === locationId && slot.isSet && slot.fitsDeclaration)
  const slot = own ?? (setting.dealership.isSet && setting.dealership.fitsDeclaration ? setting.dealership : null)
  const text =
    slot?.value?.number ?? (setting.declaration.whenNotSet === 'acts_as' ? setting.declaration.actsAs?.number : null)
  if (text == null) return null
  const number = Number(text)
  return Number.isFinite(number) ? number : null
}

/**
 * The number rules of one setting in one column, or undefined when it has none.
 * A location's column is checked there; the Betrieb's at the Betrieb and at
 * every active location without a value of its own for this setting, since the
 * Betrieb's value is in force there (the store checks the same scopes).
 */
export function numberRuleAt(
  data: AreaSettingsRead,
  setting: AreaSettingRead,
  locationId: string | null,
): NumberRule | undefined {
  const { declaration } = setting
  const dependents = data.settings.filter((other) => other.declaration.multipleOf === declaration.key)
  if (declaration.divides == null && declaration.multipleOf == null && dependents.length === 0) return undefined

  const scopes: (string | null)[] =
    locationId !== null
      ? [locationId]
      : [
          null,
          ...data.locations
            .filter((location) => location.isActive)
            // A stale value of the location reads as not set (KAN-842): the Betrieb's value is in force there.
            .filter((location) => !setting.locations.some((slot) => slot.locationId === location.id && slot.isSet && slot.fitsDeclaration))
            .map((location) => location.id),
        ]
  const unique = (bounds: RuleBound[]) =>
    bounds.filter((bound, index) => bounds.findIndex((b) => b.value === bound.value && b.key === bound.key) === index)

  const divides: RuleBound[] = []
  if (declaration.divides != null) divides.push({ value: Number(declaration.divides), key: null })
  for (const dependent of dependents) {
    for (const scope of scopes) {
      const value = numberInForceAt(data, dependent.declaration.key, scope)
      if (value !== null) divides.push({ value, key: dependent.declaration.key })
    }
  }
  const multipleOf: RuleBound[] = []
  const partner = declaration.multipleOf ?? null
  if (partner !== null) {
    for (const scope of scopes) {
      const value = numberInForceAt(data, partner, scope)
      if (value !== null) multipleOf.push({ value, key: partner })
    }
  }
  return {
    divides: unique(divides),
    multipleOf: unique(multipleOf),
    multipleOfNotInForce: partner !== null && multipleOf.length === 0 ? partner : null,
  }
}

/** The declaration's own `divides` (O6: a divisor of 60), or null: the one number rule
 * that needs no other setting. */
export function declarationDivisorViolation(rule: NumberRule, value: number): RuleViolation | null {
  if (!whole(value)) return null
  const own = rule.divides.find((bound) => bound.key === null)
  return own && whole(own.value) && own.value % value !== 0 ? { kind: 'notADivisor', bound: own } : null
}

// (key, location id or '') -> the number in force in that slot, null while it is not set or no
// longer fits; a slot that is not stored is absent (setting_rules._slots_of).
type Slots = Map<string, number | null>
const slotKey = (key: string, locationId: string | null) => `${key}|${locationId ?? ''}`

function numberOf(slot: { value?: { number?: string | null } | null; fitsDeclaration: boolean }): number | null {
  const text = slot.fitsDeclaration ? slot.value?.number : null
  if (text == null) return null
  const number = Number(text)
  return Number.isFinite(number) ? number : null
}

function slotsOf(settings: AreaSettingRead[]): Slots {
  const slots: Slots = new Map()
  for (const setting of settings) {
    const key = setting.declaration.key
    if (setting.dealership.isSet) slots.set(slotKey(key, null), numberOf(setting.dealership))
    for (const slot of setting.locations) if (slot.isSet) slots.set(slotKey(key, slot.locationId), numberOf(slot))
  }
  return slots
}

function effective(slots: Slots, setting: AreaSettingRead, scope: string | null): number | null {
  const key = setting.declaration.key
  let value = scope !== null ? (slots.get(slotKey(key, scope)) ?? null) : null
  if (value === null) value = slots.get(slotKey(key, null)) ?? null
  if (value === null && setting.declaration.whenNotSet === 'acts_as') {
    const text = setting.declaration.actsAs?.number
    value = text != null && Number.isFinite(Number(text)) ? Number(text) : null
  }
  return value
}

function notAMultiple(slots: Slots, multiple: AreaSettingRead, divisor: AreaSettingRead, scope: string | null): boolean {
  const value = effective(slots, multiple, scope)
  const of = effective(slots, divisor, scope)
  if (value === null || of === null || of === 0) return false
  return value % of !== 0
}

/**
 * The refusal `check_multiple_rules` would give writing `proposed` (null: a clear) to
 * `key`'s slot at `locationId` (null: the Betrieb), or null. Exactly the store's: the
 * scopes are the Betrieb and every active location holding a slot of either key, and only
 * a scope that is broken after the write and was not before refuses («never worse»). A
 * rule whose other key is of another area is not in `data`: there the API's refusal shows.
 */
export function multipleRuleViolation(
  data: AreaSettingsRead,
  key: string,
  locationId: string | null,
  proposed: number | null,
): RuleViolation | null {
  const byKey = new Map(data.settings.map((s) => [s.declaration.key, s]))
  const rules = data.settings
    .filter((s) => s.declaration.multipleOf != null)
    .map((s) => [s, byKey.get(s.declaration.multipleOf!)] as const)
    .filter((pair): pair is readonly [AreaSettingRead, AreaSettingRead] => pair[1] !== undefined)
    .filter(([multiple, divisor]) => key === multiple.declaration.key || key === divisor.declaration.key)
  if (rules.length === 0) return null
  const keys = new Set(rules.flatMap(([m, d]) => [m.declaration.key, d.declaration.key]))
  const before = slotsOf(data.settings.filter((s) => keys.has(s.declaration.key)))
  const after: Slots = new Map(before)
  after.set(slotKey(key, locationId), proposed)
  const held = new Set([...after.keys()].map((k) => k.slice(k.indexOf('|') + 1)).filter(Boolean))
  const scopes: (string | null)[] = [null, ...data.locations.filter((l) => l.isActive && held.has(l.id)).map((l) => l.id)]

  for (const [multiple, divisor] of rules) {
    for (const scope of scopes) {
      if (notAMultiple(after, multiple, divisor, scope) && !notAMultiple(before, multiple, divisor, scope)) {
        const multipleValue = effective(after, multiple, scope)!
        const divisorValue = effective(after, divisor, scope)!
        return key === multiple.declaration.key
          ? { kind: 'notAMultiple', bound: { value: divisorValue, key: divisor.declaration.key }, value: multipleValue }
          : { kind: 'wouldNotDivide', bound: { value: multipleValue, key: multiple.declaration.key }, value: divisorValue }
      }
    }
  }
  return null
}
