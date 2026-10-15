/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-358: `toISOString().slice(0, 10)` is the UTC calendar day. Between
// midnight and 01:00 (02:00 in summer) Swiss time it is yesterday, so a
// purchase, a cost or an accessory pre-filled with it was booked a day
// early. Today's day goes through `todayLocalIsoDate` (utils/format.ts).

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

const UTC_DAY = /\.toISOString\(\)\s*\.(?:slice\(\s*0\s*,\s*10\s*\)|substring\(\s*0\s*,\s*10\s*\)|split\(\s*['"]T['"]\s*\)\s*\[\s*0\s*\])/

function offendingLines(source: string): string[] {
  const out: string[] = []
  source.split('\n').forEach((line, index) => {
    if (/^\s*(?:\/\/|\*)/.test(line)) return
    if (UTC_DAY.test(line)) out.push(`${index + 1}: ${line.trim()}`)
  })
  return out
}

describe("today's date is the local calendar day (KAN-358)", () => {
  it('no file in apps/dms/src takes the UTC day from toISOString()', () => {
    const files = collectFiles(SRC_ROOT)
    // An empty walk would pass vacuously.
    expect(files.some((file) => file.endsWith('/components/stock-detail/RecordPurchaseDialog.tsx'))).toBe(true)

    const offenders = files.flatMap((file) =>
      offendingLines(readFileSync(file, 'utf-8')).map((line) => `${file.slice(SRC_ROOT.length + 1)}:${line}`)
    )
    expect(offenders).toEqual([])
  })

  it('matches the shapes it exists to stop', () => {
    expect(offendingLines('useState(() => new Date().toISOString().slice(0, 10))')).toHaveLength(1)
    expect(offendingLines("const day = now.toISOString().split('T')[0]")).toHaveLength(1)
    expect(offendingLines('const day = now.toISOString().substring(0, 10)')).toHaveLength(1)

    expect(offendingLines('useState(() => todayLocalIsoDate())')).toEqual([])
    expect(offendingLines('// `new Date().toISOString().slice(0, 10)` is the UTC day')).toEqual([])
    expect(offendingLines('createdAt: new Date().toISOString(),')).toEqual([])
  })
})
