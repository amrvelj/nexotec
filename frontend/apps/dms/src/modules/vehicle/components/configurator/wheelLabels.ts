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
  tyre: { rims?: string | null; rimWidth?: number | string | null; rimSize?: number | null; rimOffset?: number | null },
): string {
  // A row naming width and diameter but no designation still shows the rim,
  // in the designation's own notation («7.5Jx18»).
  const rims = tyre.rims || (tyre.rimWidth != null && tyre.rimSize != null ? `${Number(tyre.rimWidth)}Jx${tyre.rimSize}` : '')
  if (!rims) return ''
  return tyre.rimOffset != null ? t('configurator.wheels.rimValue', { rims, offset: tyre.rimOffset }) : rims
}
