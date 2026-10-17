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
// plain variable whose nearest `const` declaration before the prop, read to
// the end of its own statement, falls back to `defaultColumnLayout(...)`.

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

/** The text and position of a prop's `{…}` value on each `<Tag …>` in `source`. */
function propValues(source: string, tag: string, prop: string): ({ value: string; at: number } | null)[] {
  const values: ({ value: string; at: number } | null)[] = []
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
    values.push({ value: attributes.slice(start, i - 1).trim(), at: match.index + start })
  }
  return values
}

/** The initializer of a `const` declaration starting at `from`: up to a `;`
 * or a line break at bracket depth 0 that does not continue the expression. */
function initializerAt(source: string, from: number): string {
  let begin = from
  while (begin < source.length && /\s/.test(source[begin])) begin += 1
  let depth = 0
  let i = begin
  for (; i < source.length; i += 1) {
    const ch = source[i]
    if ('([{'.includes(ch)) depth += 1
    else if (')]}'.includes(ch)) depth -= 1
    else if (depth === 0 && ch === ';') break
    else if (depth === 0 && ch === '\n') {
      const before = source.slice(begin, i).trimEnd()
      const after = source.slice(i + 1).trimStart()
      const continues = /(\?\?|\|\||&&|[=(,?:+.])$/.test(before) || /^(\?\?|\|\||&&|[?:.)\]}])/.test(after)
      if (!continues) break
    }
  }
  return source.slice(begin, i)
}

/** True when the nearest `const name = …` before `at` falls back to `defaultColumnLayout(...)`. */
function fallsBackToDefault(source: string, name: string, at: number): boolean {
  const declaration = new RegExp(`\\bconst\\s+${name}\\b[^=\\n]*=`, 'g')
  let nearest: number | null = null
  for (let match = declaration.exec(source); match && match.index < at; match = declaration.exec(source)) {
    nearest = match.index + match[0].length
  }
  return nearest !== null && /\?\?\s*defaultColumnLayout\(/.test(initializerAt(source, nearest))
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
    for (const found of values) {
      if (found === null) out.push(`${what} gets no ${prop}`)
      else if (!/^[A-Za-z_$][\w$]*$/.test(found.value) || !fallsBackToDefault(source, found.value, found.at))
        out.push(`${what}'s ${prop}={${found.value}} does not fall back to defaultColumnLayout(...)`)
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
    // A value declared just above a correct fallback does not borrow it.
    expect(problems(`${hidden}${decl}${panel('columnLayout')}${grid('savedLayout')}`)).toHaveLength(1)
    // A saved layout renamed, then given to the fallback and to the grid.
    const renamed = `${hidden}const columnLayout = useMemo(() => savedLayout ?? defaultColumnLayout(r), [x])\n`
    expect(problems(`${renamed}${panel('columnLayout')}${grid('savedLayout')}`)).toHaveLength(1)
    // A second component in the file reusing the name with the KAN-361 value.
    const second = `const columnLayout = gridPrefs.columnLayout ?? undefined\n${panel('columnLayout')}${grid('columnLayout')}`
    expect(problems(`${good}${second}`)).toHaveLength(2)
    // A fallback spread over lines is still one statement.
    expect(problems(`const columnLayout =\n  gridPrefs.columnLayout\n  ?? defaultColumnLayout(columnRegistry)\n${panel('columnLayout')}${grid('columnLayout')}`)).toEqual([])
    // No layout on the grid at all.
    expect(problems(`${decl}${panel('columnLayout')}<DataGrid rows={rows} />\n`)).toHaveLength(1)
    // A file without a Columns panel is not this rule's business.
    expect(problems('<DataGrid columnLayout={gridPrefs.columnLayout ?? undefined} />')).toEqual([])
  })
})
