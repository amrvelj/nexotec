/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-333: a date-only value (`1980-05-01`, a backend `dt.date`) names a
// calendar day. formatDate renders it in the browser's zone from UTC
// midnight, so anywhere west of Greenwich it shows the day before; it goes
// through formatDateOnly (KAN-180). formatDate/formatDateTime stay for
// timestamps. This scan refuses either of them on a date-only field.
//
// A date-only field: the backend's `dt.date` response fields as the frontend
// names them. Every one ending in Date, Since or FollowUp is caught by name,
// and two that do not end that way are listed. Not every date-only field can
// be judged by its name (KAN-363): `validFrom` and `validTo` are a date on a
// plate, an accessory, a user's access window and a valuation (which no screen
// renders yet) but a timestamp on a contact channel, and `lastDeltaCursor`
// (the catalogue sync's watermark) has no date-like name at all. No name rule
// covers those three; the render tests of the screens that show them do,
// under TZ=Pacific/Honolulu: dateOnlyTabs (a plate's and an accessory's
// validity), UsersListPage.dateOnly (a user's access window) and
// IntegrationPlatformView.pending (the watermark).
const DATE_ONLY_SUFFIX = /(?:Date|Since|FollowUp)$/
const DATE_ONLY_NAMES = new Set(['expectedDelivery', 'vehicleFirstRegistration'])

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

// `formatDate(row.original.purchaseDate, locale)` → the argument's last
// identifier, `purchaseDate`; `date('birthDate', …, (row) => row.birthDate)`
// in CustomersListPage passes its formatter as a fourth argument instead,
// which the second pattern checks.
const TIMESTAMP_CALL = /\bformatDate(?:Time)?\(\s*([\w.?!]+)/g
const CUSTOMER_DATE_COLUMN = /\bdate\('(\w+)',[^\n]*\)\s*,?\s*$/
// KAN-358 — a date-only field rendered raw shows `2021-04-01`, not
// `01.04.2021`; KAN-333 fixed six such fields by hand. Caught, and only in
// these positions so that code rendering nothing is never flagged:
// - a JSX child: `>{r.readingDate}` or a line that is only
//   `{vehicle.firstRegistrationDate ?? '—'}`; an attribute value
//   (`value={…}`, an editor keeps the ISO value), a destructuring or an
//   object literal is never a JSX child;
// - a grid column's `cell:` renderer returning the bare field.
// Not caught, because a static scan cannot tell them from a correct use:
// a field joined into a string (`[a, r.firstRegistrationDate].join(' · ')`),
// a field passed as a value to a row or card helper
// (`value: c.firstRegistrationDate ?? '—'`, ConfigurationSummaryCard's
// shape), and an `InlineEditField` whose `value=` is both the editor's ISO
// value and its display (the Identity-tab shape). Not caught either, because
// the patterns stop short of them (KAN-363): a JSX child that shares its
// element with text or another expression
// (`<Text>{t('x')}: {r.purchaseDate}</Text>`), and a fallback that is not a
// string literal or a `t(…)` call (`r.purchaseDate ?? null`,
// `r.purchaseDate ?? EMPTY`). This list is the misses known so far, not a
// proof that there are no others; a screen that renders a date-only field
// needs its own render test either way.
const FALLBACK = String.raw`(?:\s*(?:\?\?|\|\|)\s*(?:'[^']*'|"[^"]*"|t\([^)]*\)))?`
const RAW_JSX_CHILD = new RegExp(String.raw`(?:>\s*|^\s*)\{\s*([\w.?!]+)` + FALLBACK + String.raw`\s*\}(?=\s*(?:<|$))`, 'g')
const RAW_CELL_RETURN = new RegExp(String.raw`\bcell:\s*\([^)]*\)\s*=>\s*([\w.?!]+)` + FALLBACK + String.raw`\s*,?\s*$`)

function isDateOnly(name: string): boolean {
  return DATE_ONLY_SUFFIX.test(name) || DATE_ONLY_NAMES.has(name)
}

function offendingLines(source: string): string[] {
  const out: string[] = []
  source.split('\n').forEach((line, index) => {
    if (/^\s*import\b/.test(line)) return
    const lastName = (expr: string) => expr.split(/[.?!]+/).filter(Boolean).pop() ?? ''
    for (const match of line.matchAll(RAW_JSX_CHILD)) {
      if (isDateOnly(lastName(match[1]))) out.push(`${index + 1}: ${line.trim()}`)
    }
    const arrow = RAW_CELL_RETURN.exec(line)
    if (arrow && isDateOnly(lastName(arrow[1]))) out.push(`${index + 1}: ${line.trim()}`)
    for (const match of line.matchAll(TIMESTAMP_CALL)) {
      const field = match[1].split(/[.?!]+/).filter(Boolean).pop() ?? ''
      if (isDateOnly(field)) out.push(`${index + 1}: ${line.trim()}`)
    }
    const column = CUSTOMER_DATE_COLUMN.exec(line)
    if (column && isDateOnly(column[1]) && !line.includes('formatDateOnly')) out.push(`${index + 1}: ${line.trim()}`)
  })
  return out
}

describe('date-only fields render through formatDateOnly (KAN-333)', () => {
  it('no formatDate / formatDateTime call in apps/dms/src takes a date-only field, and none is rendered raw', () => {
    const files = collectFiles(SRC_ROOT)
    // An empty walk would pass vacuously.
    expect(files.some((file) => file.endsWith('/pages/StockListPage.tsx'))).toBe(true)

    const offenders = files.flatMap((file) =>
      offendingLines(readFileSync(file, 'utf-8')).map((line) => `${file.slice(SRC_ROOT.length + 1)}:${line}`)
    )
    expect(offenders).toEqual([])
  })

  // The scan passes on an empty result as easily as on a clean tree — this
  // proves it fires on the shapes KAN-333 removed and spares timestamps.
  it('matches the shapes it exists to stop', () => {
    expect(offendingLines("{item.purchaseDate ? formatDate(item.purchaseDate, locale) : '—'}")).toHaveLength(1)
    expect(offendingLines('formatDate(row.original.expectedDelivery, locale)')).toHaveLength(1)
    expect(offendingLines('formatDate(valuation.vehicleFirstRegistration, locale)')).toHaveLength(1)
    expect(offendingLines('formatDateTime(customer.nextFollowUp)')).toHaveLength(1)
    expect(offendingLines("      date('birthDate', t('x'), (row) => row.birthDate),")).toHaveLength(1)

    expect(offendingLines('formatDateOnly(row.original.purchaseDate, locale)')).toEqual([])
    expect(offendingLines("      date('birthDate', t('x'), (row) => row.birthDate, formatDateOnly),")).toEqual([])
    expect(offendingLines("      date('createdAt', t('x'), (row) => row.createdAt),")).toEqual([])
    expect(offendingLines('formatDate(row.original.updatedAt, locale)')).toEqual([])
    expect(offendingLines('formatDate(valuation.validUntil, locale)')).toEqual([])

    // KAN-358 — a date-only field rendered raw, the shape KAN-333 removed by hand.
    expect(offendingLines('<Text>{vehicle.firstRegistrationDate}</Text>')).toHaveLength(1)
    expect(offendingLines("<Text>{item.purchaseDate ?? '—'}</Text>")).toHaveLength(1)
    expect(offendingLines('      cell: ({ row }) => row.original.firstRegistrationDate,')).toHaveLength(1)

    expect(offendingLines("            {r.purchaseDate || '—'}")).toHaveLength(1)
    // The OdometerTab line KAN-333 fixed, as it stood before (46b7d35).
    expect(offendingLines('                <Table.Td>{r.readingDate}</Table.Td>')).toHaveLength(1)

    expect(offendingLines('<TextInput type="date" value={draft.firstRegistrationDate} />')).toEqual([])
    // Code that renders nothing is never a raw render.
    expect(offendingLines('  const { purchaseDate } = draft')).toEqual([])
    expect(offendingLines('export function Row({ purchaseDate }: Props) {')).toEqual([])
    expect(offendingLines('    await patch({ purchaseDate })')).toEqual([])
    expect(offendingLines('      sortValue: (row) => row.purchaseDate,')).toEqual([])
    expect(offendingLines('      accessorFn: (row) => row.purchaseDate,')).toEqual([])
    expect(offendingLines("import { formatDate } from '../utils/format'")).toEqual([])
    expect(offendingLines('<Text>{vehicle.vin}</Text>')).toEqual([])
  })
})
