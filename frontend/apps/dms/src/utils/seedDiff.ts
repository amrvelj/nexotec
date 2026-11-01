/**
 * KAN-209 — the Translations panel's "diff against seed": where a text
 * differs from the one it was seeded from, as one changed stretch between the
 * common start and the common end (a translation fix is one edit far more
 * often than several, and this reads at a glance). Works on whole words where
 * it can, so «Betrieb» → «Garage» shows the word, not «Betr|ieb».
 */
export interface SeedDiff {
  same: boolean
  prefix: string
  removed: string
  added: string
  suffix: string
}

function tokens(text: string): string[] {
  return text.match(/\s+|[^\s]+/g) ?? []
}

export function diffAgainstSeed(seed: string, value: string): SeedDiff {
  if (seed === value) return { same: true, prefix: value, removed: '', added: '', suffix: '' }
  const a = tokens(seed)
  const b = tokens(value)
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--
    endB--
  }
  return {
    same: false,
    prefix: a.slice(0, start).join(''),
    removed: a.slice(start, endA).join(''),
    added: b.slice(start, endB).join(''),
    suffix: a.slice(endA).join(''),
  }
}
