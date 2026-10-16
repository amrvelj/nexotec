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
// every column (ADR-060: a documented subset is visible by default; ADR-092:
// DataGrid itself stays layout-agnostic). In a file that renders the Columns
// panel, the panel's `layout` and DataGrid's `columnLayout` must each be a
// plain variable whose declaration falls back to `defaultColumnLayout(...)`.

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

/** The text of a prop's `{…}` value on each `<Tag …>` in `source`. */
function propValues(source: string, tag: string, prop: string): (string | null)[] {
  const values: (string | null)[] = []
  const opening = new RegExp(`<${tag}\\b`, 'g')
  for (let match = opening.exec(source); match; match = opening.exec(source)) {
    // The tag's attributes run to its first `/>` or `>` at brace depth 0,
    // after a type argument (`<DataGrid<StockItemRead>`) if there is one.
    let depth = 0
    let end = match.index + match[0].length
    if (source[end] === '<') end = source.indexOf('>', end) + 1
    for (; end < source.length; end += 1) {
      const ch = source[end]
      if (ch === '{') depth += 1
      else if (ch === '}') depth -= 1
      else if (depth === 0 && ch === '>') break
    }
    const attributes = source.slice(match.index, end)
    const prop_ = new RegExp(`\\b${prop}=\\{`).exec(attributes)
    if (!prop_) {
      values.push(null)
      continue
    }
    let valueDepth = 1
    let i = prop_.index + prop_[0].length
    const start = i
    for (; i < attributes.length && valueDepth > 0; i += 1) {
      if (attributes[i] === '{') valueDepth += 1
      else if (attributes[i] === '}') valueDepth -= 1
    }
    values.push(attributes.slice(start, i - 1).trim())
  }
  return values
}

/** True when `name` is declared (`const name = …`) as a value that falls back to `defaultColumnLayout(...)`. */
function fallsBackToDefault(source: string, name: string): boolean {
  const declaration = new RegExp(`\\bconst\\s+${name}\\b[^=]*=([\\s\\S]{0,240})`).exec(source)
  return Boolean(declaration && /\?\?\s*defaultColumnLayout\(/.test(declaration[1]))
}

function problems(source: string): string[] {
  if (!/<ColumnConfigPanel\b/.test(source)) return []
  const checks: [string, string, string][] = [
    ['ColumnConfigPanel', 'layout', 'the Columns panel'],
    ['DataGrid', 'columnLayout', 'DataGrid'],
  ]
  const out: string[] = []
  for (const [tag, prop, what] of checks) {
    const values = propValues(source, tag, prop)
    if (values.length === 0) out.push(`renders ColumnConfigPanel but no ${tag}`)
    for (const value of values) {
      if (value === null) out.push(`${what} gets no ${prop}`)
      else if (!/^[A-Za-z_$][\w$]*$/.test(value) || !fallsBackToDefault(source, value))
        out.push(`${what}'s ${prop}={${value}} does not fall back to defaultColumnLayout(...)`)
    }
  }
  return out
}

describe('a grid with a Columns panel shows its default subset to a user with no saved layout (KAN-361)', () => {
  it('every file in apps/dms/src that renders ColumnConfigPanel gives it and DataGrid the defaultColumnLayout fallback', () => {
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
    const decl = 'const columnLayout = useMemo(\n  () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),\n  [x]\n)\n'
    const panel = (layout: string) => `<ColumnConfigPanel registry={r} layout={${layout}} labels={l} />\n`
    const grid = (layout: string) => `<DataGrid<Row>\n  rows={rows}\n  columnLayout={${layout}}\n  labels={{ a: t('x') }}\n/>\n`
    const good = `${decl}${panel('columnLayout')}${grid('columnLayout')}`
    expect(problems(good)).toEqual([])

    // The KAN-361 defect, inline or split over lines.
    expect(problems(`${decl}${panel('columnLayout')}${grid('gridPrefs.columnLayout ?? undefined')}`)).toHaveLength(1)
    expect(problems(`${decl}${panel('columnLayout')}${grid('gridPrefs.columnLayout\n    ?? undefined')}`)).toHaveLength(1)
    // A bare saved layout to either side.
    expect(problems(`${decl}${panel('gridPrefs.columnLayout')}${grid('columnLayout')}`)).toHaveLength(1)
    expect(problems(`${decl}${panel('columnLayout')}${grid('gridPrefs.columnLayout')}`)).toHaveLength(1)
    // The same behind a variable.
    const hidden = 'const savedLayout = gridPrefs.columnLayout ?? undefined\n'
    expect(problems(`${hidden}${panel('savedLayout')}${grid('savedLayout')}`)).toHaveLength(2)
    // No layout on the grid at all.
    expect(problems(`${decl}${panel('columnLayout')}<DataGrid rows={rows} />\n`)).toHaveLength(1)
    // A file without a Columns panel is not this rule's business.
    expect(problems('<DataGrid columnLayout={gridPrefs.columnLayout ?? undefined} />')).toEqual([])
  })
})
