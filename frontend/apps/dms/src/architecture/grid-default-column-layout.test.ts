/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-361: DataGrid without a `columnLayout` renders every column and
// ignores `defaultVisible`, so a grid that passed
// `gridPrefs.columnLayout ?? undefined` showed a user with no saved layout
// every column (ADR-060: a documented subset is visible by default). A file
// that renders the Columns panel builds its fallback with
// `defaultColumnLayout(...)` and never hands `undefined` to the grid or the
// panel in place of a layout.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (/\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry)) out.push(full)
  }
  return out
}

const NO_LAYOUT_PROP = /\b(?:columnLayout|layout)=\{\s*gridPrefs\.columnLayout\s*\?\?\s*(?:undefined|null|\{)/

function problems(source: string): string[] {
  if (!/<ColumnConfigPanel\b/.test(source)) return []
  const out: string[] = []
  if (!/\bdefaultColumnLayout\(/.test(source)) out.push('renders ColumnConfigPanel without defaultColumnLayout(...)')
  source.split('\n').forEach((line, index) => {
    if (NO_LAYOUT_PROP.test(line)) out.push(`${index + 1}: ${line.trim()}`)
  })
  return out
}

describe('a grid with a Columns panel shows its default subset to a user with no saved layout (KAN-361)', () => {
  it('every file in apps/dms/src that renders ColumnConfigPanel falls back to defaultColumnLayout', () => {
    const files = collectFiles(SRC_ROOT)
    const withPanel = files.filter((file) => /<ColumnConfigPanel\b/.test(readFileSync(file, 'utf-8')))
    // An empty walk would pass vacuously: the six grids with a panel today.
    expect(withPanel.length).toBeGreaterThanOrEqual(6)

    const offenders = withPanel.flatMap((file) =>
      problems(readFileSync(file, 'utf-8')).map((problem) => `${file.slice(SRC_ROOT.length + 1)}: ${problem}`)
    )
    expect(offenders).toEqual([])
  })

  it('matches the shapes it exists to stop', () => {
    const panel = '<ColumnConfigPanel registry={r} layout={columnLayout} />\n'
    const good = `const columnLayout = gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry)\n${panel}`
    expect(problems(good)).toEqual([])
    expect(problems(`${good}<DataGrid columnLayout={gridPrefs.columnLayout ?? undefined} />`)).toHaveLength(1)
    expect(problems(`${good}<ColumnConfigPanel layout={gridPrefs.columnLayout ?? {`)).toHaveLength(1)
    expect(problems(panel)).toHaveLength(1)
    expect(problems('<DataGrid columnLayout={gridPrefs.columnLayout ?? undefined} />')).toEqual([])
  })
})
