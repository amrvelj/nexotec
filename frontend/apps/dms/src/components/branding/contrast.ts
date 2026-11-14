/**
 * KAN-205 — the brand colours' contrast, while the manager picks: WCAG 2.x
 * AA for normal text, 4.5:1. The API judges the same three pairs on every
 * save and returns its verdict (`app/platform/organisation/services/brand_colors.py`);
 * this is the same formula, so the verdict on screen before the save is the
 * one the save returns. Both sides share their test vectors.
 */
export const WCAG_AA_NORMAL_TEXT = 4.5
/** White paper's relative luminance — the background of every document. */
const WHITE_LUMINANCE = 1
const HEX = /^#[0-9A-Fa-f]{6}$/

export type ContrastPairName = 'primary_on_white' | 'secondary_on_white' | 'secondary_on_primary'

export interface ContrastPair {
  pair: ContrastPairName
  foreground: string
  /** The other colour of the pair; null is white paper (the API sends white as hex). */
  background: string | null
  /** Rounded to two decimals, as shown. */
  ratio: number
  /** Judged on the unrounded ratio. */
  passes: boolean
}

export const isHexColor = (value: string): boolean => HEX.test(value)

function channel(value: number): number {
  const c = value / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

export function relativeLuminance(hex: string): number {
  if (!isHexColor(hex)) throw new Error(`not a #RRGGBB colour: ${hex}`)
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

const ratioOf = (la: number, lb: number) => (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)

export function contrastRatio(a: string, b: string): number {
  return ratioOf(relativeLuminance(a), relativeLuminance(b))
}

/** The pairs the API judges; a colour that is not #RRGGBB yet is not judged. */
export function judgeBrandColors(primary: string, secondary: string | null): ContrastPair[] {
  if (!isHexColor(primary)) return []
  const pairs: [ContrastPairName, string, string | null][] = [['primary_on_white', primary, null]]
  if (secondary && isHexColor(secondary)) {
    pairs.push(['secondary_on_white', secondary, null], ['secondary_on_primary', secondary, primary])
  }
  return pairs.map(([pair, foreground, background]) => {
    const ratio = ratioOf(relativeLuminance(foreground), background === null ? WHITE_LUMINANCE : relativeLuminance(background))
    return {
      pair,
      foreground: foreground.toUpperCase(),
      background: background?.toUpperCase() ?? null,
      ratio: Math.round(ratio * 100) / 100,
      passes: ratio >= WCAG_AA_NORMAL_TEXT,
    }
  })
}
