import type { TFunction } from 'i18next'

// KAN-75: the axle is the `axle_position` value the provider's AchsenCode
// resolves to through the code map, or null while that code is an unresolved
// mapping gap (or the provider sent none) — then it reads «not classified»,
// never a guessed axle and never the raw code.
const AXLE_I18N_KEY: Record<string, string> = { front: 'front', rear: 'rear', both: 'both' }

export function axleLabel(t: TFunction, axle: string | null | undefined): string {
  const key = axle == null ? undefined : AXLE_I18N_KEY[axle]
  return t(`configurator.wheels.axlePosition.${key ?? 'unclassified'}`)
}

/** KAN-596 (FR-C-08): the rim as the provider names it, with its offset (ET)
 * where one is sent; empty when the row names no rim. */
export function rimLabel(
  t: TFunction,
  tyre: { rims?: string | null; rimOffset?: number | null },
): string {
  if (!tyre.rims) return ''
  return tyre.rimOffset != null ? t('configurator.wheels.rimValue', { rims: tyre.rims, offset: tyre.rimOffset }) : tyre.rims
}
