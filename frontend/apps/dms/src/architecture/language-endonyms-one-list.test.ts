/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-343 (Anto, 2026-11-03, option A): the four languages are named in
// themselves for every user, and that is the one ruled exception to "no
// hardcoded user-visible string". The exception holds for exactly one list,
// i18n/languageEndonyms.ts: ReferenceDataPage and customerOptions.ts each
// carried their own literals before, and a third copy would drift from both.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const THE_LIST = join(SRC_ROOT, 'i18n', 'languageEndonyms.ts')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git', 'locales'])

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

// A string literal or a JSX text that is exactly one of the four names.
const ENDONYM_LITERAL = /(?:['"`]\s*(?:Deutsch|Français|Italiano|English)\s*['"`])|(?:>\s*(?:Deutsch|Français|Italiano|English)\s*<)/

function offendingLines(source: string): string[] {
  const out: string[] = []
  source.split('\n').forEach((line, index) => {
    if (/^\s*(?:\/\/|\*)/.test(line)) return
    if (ENDONYM_LITERAL.test(line)) out.push(`${index + 1}: ${line.trim()}`)
  })
  return out
}

describe('language endonyms come from one list (KAN-343)', () => {
  it('no file in apps/dms/src but i18n/languageEndonyms.ts writes a language endonym', () => {
    const files = collectFiles(SRC_ROOT)
    // An empty walk would pass vacuously.
    expect(files).toContain(THE_LIST)
    expect(files.some((file) => file.endsWith('/pages/ReferenceDataPage.tsx'))).toBe(true)

    const offenders = files
      .filter((file) => file !== THE_LIST)
      .flatMap((file) => offendingLines(readFileSync(file, 'utf-8')).map((line) => `${file.slice(SRC_ROOT.length + 1)}:${line}`))
    expect(offenders).toEqual([])
  })

  it('the list itself names all four', () => {
    expect(offendingLines(readFileSync(THE_LIST, 'utf-8'))).toHaveLength(4)
  })

  it('matches the shapes it exists to stop', () => {
    expect(offendingLines("  { value: 'de', label: 'Deutsch' },")).toHaveLength(1)
    expect(offendingLines('<Table.Th>Français</Table.Th>')).toHaveLength(1)
    expect(offendingLines('<TextInput label="Italiano" value={x} />')).toHaveLength(1)
    expect(offendingLines("['DE', 'Deutschland', 'Allemagne']")).toHaveLength(0)
    expect(offendingLines('// the English fallback')).toHaveLength(0)
  })
})
