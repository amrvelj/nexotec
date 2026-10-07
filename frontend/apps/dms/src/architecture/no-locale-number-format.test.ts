/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-160: every user-visible number and CHF amount goes through
// formatNumber / formatCurrencyChf (packages/ui-kit/src/format/
// swissNumber.ts, re-exported by apps/dms/src/utils/format.ts). Anything
// else takes the runtime's CLDR data — U+2019 grouping under ICU 77, a comma
// decimal and narrow-space grouping under fr-CH, the browser's language with
// no locale at all — so the same figure looks different on two screens and
// differs from the printed PDF (`12'500`, period decimal). oxlint has no
// custom-rule mechanism, so this is a build-failing scan, like the colour one.
//
// Dates are not the number scan's business: formatDate/formatDateTime follow
// the locale tag on purpose and use Intl.DateTimeFormat, which it does not
// match. They have their own scan below (KAN-164): a date rendered with
// toLocaleDateString() and no locale follows the browser, not the UI language
// and FR-13's dd.MM.yyyy, so dates likewise go through one implementation.

const FRONTEND_ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')
const SCAN_ROOTS = ['apps/dms/src', 'packages/ui-kit/src'].map((p) => join(FRONTEND_ROOT, p))

// The one implementation of Swiss number formatting.
const EXEMPT_SUFFIXES = ['/packages/ui-kit/src/format/swissNumber.ts']

const SCAN_EXTENSIONS = ['.ts', '.tsx']
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

const FORBIDDEN: { name: string; pattern: RegExp }[] = [
  { name: 'toLocaleString', pattern: /\.toLocaleString\s*\(/ },
  { name: 'Intl.NumberFormat', pattern: /\bIntl\s*\.\s*NumberFormat\b/ },
  { name: "Mantine's NumberFormatter", pattern: /\bNumberFormatter\b/ },
]

// The one implementation of date formatting (formatDate / formatDateTime).
const DATE_EXEMPT_SUFFIXES = ['/apps/dms/src/utils/format.ts']

const FORBIDDEN_DATE: { name: string; pattern: RegExp }[] = [
  { name: 'toLocaleDateString', pattern: /\.toLocaleDateString\s*\(/ },
  { name: 'toLocaleTimeString', pattern: /\.toLocaleTimeString\s*\(/ },
  { name: 'Intl.DateTimeFormat', pattern: /\bIntl\s*\.\s*DateTimeFormat\b/ },
]

const BARE_NUMERIC_PLACEHOLDER = /\{\{\s*(count|max)\s*\}\}/

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      collectFiles(full, out)
    } else if (SCAN_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full)
    }
  }
  return out
}

function offendingLines(source: string, forbidden = FORBIDDEN): string[] {
  const out: string[] = []
  source.split('\n').forEach((line, index) => {
    for (const { name, pattern } of forbidden) {
      if (pattern.test(line)) out.push(`${index + 1}: ${name}: ${line.trim()}`)
    }
  })
  return out
}

describe('numbers and dates are formatted only through the shared formatters', () => {
  it('finds no locale-dependent number formatting in apps/dms/src or packages/ui-kit/src', () => {
    const offenders: string[] = []
    const files = SCAN_ROOTS.flatMap((root) => collectFiles(root))
    // An empty walk would pass vacuously — the swissNumber.ts exemption
    // proves the kit root was actually scanned.
    expect(files.some((file) => EXEMPT_SUFFIXES.some((suffix) => file.endsWith(suffix)))).toBe(true)

    for (const file of files) {
      if (EXEMPT_SUFFIXES.some((suffix) => file.endsWith(suffix))) continue
      // Tests may need to name or emulate Intl to assert against it
      // (format.test.ts emulates newer CLDR data on Intl.NumberFormat).
      if (/\.test\.tsx?$/.test(file)) continue
      for (const line of offendingLines(readFileSync(file, 'utf-8'))) {
        offenders.push(`${file.slice(FRONTEND_ROOT.length + 1)}:${line}`)
      }
    }

    expect(offenders).toEqual([])
  })

  it('finds no date formatting outside formatDate / formatDateTime', () => {
    const offenders: string[] = []
    const files = SCAN_ROOTS.flatMap((root) => collectFiles(root))
    // As above: the exemption proves apps/dms was actually scanned.
    expect(files.some((file) => DATE_EXEMPT_SUFFIXES.some((suffix) => file.endsWith(suffix)))).toBe(true)

    for (const file of files) {
      if (DATE_EXEMPT_SUFFIXES.some((suffix) => file.endsWith(suffix))) continue
      if (/\.test\.tsx?$/.test(file)) continue
      for (const line of offendingLines(readFileSync(file, 'utf-8'), FORBIDDEN_DATE)) {
        offenders.push(`${file.slice(FRONTEND_ROOT.length + 1)}:${line}`)
      }
    }

    expect(offenders).toEqual([])
  })

  // Counts in translated strings: `{{count}}` alone is interpolated raw
  // (`12500`); `{{count, number}}` goes through i18n/index.ts's Swiss
  // formatter. `count` and `max` are the numeric variables the bundles use.
  it('every count and max in the locale bundles is formatted with `, number`', () => {
    const localesDir = join(FRONTEND_ROOT, 'apps/dms/src/i18n/locales')
    const offenders: string[] = []
    for (const file of readdirSync(localesDir).filter((f) => f.endsWith('.json'))) {
      readFileSync(join(localesDir, file), 'utf-8')
        .split('\n')
        .forEach((line, index) => {
          if (BARE_NUMERIC_PLACEHOLDER.test(line)) offenders.push(`${file}:${index + 1}: ${line.trim()}`)
        })
    }
    expect(offenders).toEqual([])
    expect(BARE_NUMERIC_PLACEHOLDER.test('"x": "{{count}} selected"')).toBe(true)
    expect(BARE_NUMERIC_PLACEHOLDER.test('"x": "{{count, number}} selected"')).toBe(false)
  })

  // The scan above passes on an empty result as easily as on a clean tree —
  // this proves each pattern fires on the shapes KAN-160 removed.
  it('each pattern matches the call shapes it exists to stop', () => {
    expect(offendingLines('{r.value.toLocaleString()}')).toHaveLength(1)
    expect(offendingLines('`CHF ${n.toLocaleString(locale)}`')).toHaveLength(1)
    expect(offendingLines("new Intl.NumberFormat('fr-CH').format(n)")).toHaveLength(1)
    expect(offendingLines(`<NumberFormatter value={n} thousandSeparator="'" />`)).toHaveLength(1)
    expect(offendingLines('formatNumber(row.original.odometerKm)')).toEqual([])
    expect(offendingLines('new Date(iso).toLocaleDateString()')).toEqual([])
  })

  it('each date pattern matches the call shapes it exists to stop', () => {
    expect(offendingLines('new Date(row.original.lastSeenAt).toLocaleDateString()', FORBIDDEN_DATE)).toHaveLength(1)
    expect(offendingLines("d.toLocaleTimeString('de-CH')", FORBIDDEN_DATE)).toHaveLength(1)
    expect(offendingLines("new Intl.DateTimeFormat('en-US').format(d)", FORBIDDEN_DATE)).toHaveLength(1)
    expect(offendingLines('formatDate(row.original.lastSeenAt, locale)', FORBIDDEN_DATE)).toEqual([])
  })
})
