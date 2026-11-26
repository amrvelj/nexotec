import type { AreaSettingRead, AreaSettingsRead, SettingDeclarationRead } from '../../api/types'

/**
 * KAN-808 (PR-1c review finding 5) — the settings store's two number rules,
 * as the area API names them on a declaration:
 * - `divides: n` — the value divides `n` without remainder (O6, the planner's
 *   grid: a divisor of 60);
 * - `multipleOf: key` — the value is a whole multiple of that key's value
 *   wherever both are in force (O7, the slot length, of O6); and so the other
 *   key, written, must still divide it.
 * The screen mirrors them before the write, as it mirrors check 49's
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

export type RuleViolation =
  | { kind: 'notADivisor'; bound: RuleBound }
  | { kind: 'notAMultiple'; bound: RuleBound }
  | { kind: 'wouldNotDivide'; bound: RuleBound }

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
            .filter((location) => !setting.locations.some((slot) => slot.locationId === location.id && slot.isSet))
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
