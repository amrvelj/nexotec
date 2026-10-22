/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// KAN-361: DataGrid without a `columnLayout` renders every column and
// ignores `defaultVisible`, so a grid that passed
// `gridPrefs.columnLayout ?? undefined` showed a user with no saved layout
// every column (ADR-060: a documented subset is visible by default; ADR-092:
// DataGrid itself stays layout-agnostic). In a file that renders the Columns
// panel, the panel's `layout` and every DataGrid's `columnLayout` must each be
// an identifier that the TypeScript checker resolves to a `const` whose value
// is `… ?? defaultColumnLayout(…)`, directly or as the body of
// `useMemo(() => …)`. The compiler parses the file, so comments, scopes and
// other kinds of binding (`let`, destructuring, parameters) are seen as they
// are.

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

/** A one-file program: enough for the checker to resolve local bindings. */
function checkerFor(fileName: string, text: string): { file: ts.SourceFile; checker: ts.TypeChecker } {
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const options: ts.CompilerOptions = { noLib: true, noResolve: true, jsx: ts.JsxEmit.Preserve }
  const host = ts.createCompilerHost(options)
  host.getSourceFile = (name) => (name === fileName ? file : undefined)
  host.fileExists = (name) => name === fileName
  host.readFile = (name) => (name === fileName ? text : undefined)
  const program = ts.createProgram({ rootNames: [fileName], options, host })
  return { file, checker: program.getTypeChecker() }
}

function unwrap(node: ts.Expression): ts.Expression {
  let current = node
  while (ts.isParenthesizedExpression(current) || ts.isAsExpression(current) || ts.isSatisfiesExpression(current)) {
    current = current.expression
  }
  return current
}

/** `x ?? defaultColumnLayout(…)`, directly or as the body of `useMemo(() => …)`. */
function isDefaultFallback(initializer: ts.Expression): boolean {
  let value = unwrap(initializer)
  if (ts.isCallExpression(value) && value.expression.getText() === 'useMemo') {
    const factory = value.arguments[0]
    if (!factory || !ts.isArrowFunction(factory) || ts.isBlock(factory.body)) return false
    value = unwrap(factory.body)
  }
  if (!ts.isBinaryExpression(value) || value.operatorToken.kind !== ts.SyntaxKind.QuestionQuestionToken) return false
  const right = unwrap(value.right)
  return ts.isCallExpression(right) && right.expression.getText() === 'defaultColumnLayout'
}

function fallsBackToDefault(expression: ts.Expression, checker: ts.TypeChecker): boolean {
  const value = unwrap(expression)
  if (!ts.isIdentifier(value)) return false
  const declaration = checker.getSymbolAtLocation(value)?.valueDeclaration
  if (!declaration || !ts.isVariableDeclaration(declaration) || !ts.isIdentifier(declaration.name)) return false
  const isConst = (ts.getCombinedNodeFlags(declaration) & ts.NodeFlags.Const) !== 0
  return isConst && declaration.initializer !== undefined && isDefaultFallback(declaration.initializer)
}

function problems(text: string, fileName = 'probe.tsx'): string[] {
  if (!/<ColumnConfigPanel\b/.test(text)) return []
  const { file, checker } = checkerFor(fileName, text)
  const wanted: Record<string, string> = { ColumnConfigPanel: 'layout', DataGrid: 'columnLayout' }
  const seen: Record<string, number> = { ColumnConfigPanel: 0, DataGrid: 0 }
  const out: string[] = []

  const visit = (node: ts.Node) => {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tag = node.tagName.getText()
      const prop = wanted[tag]
      if (prop) {
        seen[tag] += 1
        const line = file.getLineAndCharacterOfPosition(node.getStart()).line + 1
        const attribute = node.attributes.properties.find(
          (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === prop
        )
        const expression =
          attribute?.initializer && ts.isJsxExpression(attribute.initializer) ? attribute.initializer.expression : undefined
        if (!attribute) out.push(`${line}: <${tag}> gets no ${prop}`)
        else if (!expression || !fallsBackToDefault(expression, checker))
          out.push(`${line}: <${tag}> ${prop}={${expression?.getText() ?? ''}} does not resolve to a const … ?? defaultColumnLayout(…)`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  if (seen.DataGrid === 0) out.push('renders ColumnConfigPanel but no DataGrid')
  return out
}

describe('a grid with a Columns panel shows its default subset to a user with no saved layout (KAN-361)', () => {
  it('every file in apps/dms/src that renders ColumnConfigPanel gives it and DataGrid the defaultColumnLayout fallback', () => {
    const files = collectFiles(SRC_ROOT)
    const withPanel = files.filter((file) => /<ColumnConfigPanel\b/.test(readFileSync(file, 'utf-8')))
    // An empty walk would pass vacuously: the six grids with a panel today.
    expect(withPanel.length).toBeGreaterThanOrEqual(6)

    const offenders = withPanel.flatMap((file) =>
      problems(readFileSync(file, 'utf-8'), file).map((problem) => `${file.slice(SRC_ROOT.length + 1)}:${problem}`)
    )
    expect(offenders).toEqual([])
  })

  it('matches the shapes it exists to stop', () => {
    const memo = 'const columnLayout = useMemo(\n  () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),\n  [x]\n)\n'
    const panel = (layout: string) => `<ColumnConfigPanel registry={r} layout={${layout}} labels={l} />`
    const grid = (layout: string) => `<DataGrid<Row>\n  rows={rows}\n  columnLayout={${layout}}\n  labels={{ a: t('x') }}\n/>`
    const component = (body: string, panelLayout: string, gridLayout: string, name = 'Grid') =>
      `export function ${name}() {\n${body}  return (<>${panel(panelLayout)}${grid(gridLayout)}</>)\n}\n`
    const good = component(memo, 'columnLayout', 'columnLayout')
    expect(problems(good)).toEqual([])
    // Spread over lines, or without useMemo, is still the fallback.
    expect(problems(component('const columnLayout =\n  gridPrefs.columnLayout\n  ?? defaultColumnLayout(r)\n', 'columnLayout', 'columnLayout'))).toEqual([])

    // The KAN-361 defect, inline or split over lines.
    expect(problems(component(memo, 'columnLayout', 'gridPrefs.columnLayout ?? undefined'))).toHaveLength(1)
    expect(problems(component(memo, 'columnLayout', 'gridPrefs.columnLayout\n    ?? undefined'))).toHaveLength(1)
    // A bare saved layout to either side.
    expect(problems(component(memo, 'gridPrefs.columnLayout', 'columnLayout'))).toHaveLength(1)
    expect(problems(component(memo, 'columnLayout', 'gridPrefs.columnLayout'))).toHaveLength(1)
    // A value declared just above a correct fallback, with or without a comment that reads like a continuation.
    const saved = 'const savedLayout = gridPrefs.columnLayout ?? undefined\n'
    expect(problems(component(`${saved}${memo}`, 'columnLayout', 'savedLayout'))).toHaveLength(1)
    expect(problems(component(`${saved.trimEnd()} // the saved layout, if any.\n${memo}`, 'columnLayout', 'savedLayout'))).toHaveLength(1)
    // A saved layout renamed, then given to the fallback and to the grid.
    const renamed = `${saved}const columnLayout = useMemo(() => savedLayout ?? defaultColumnLayout(r), [x])\n`
    expect(problems(component(renamed, 'columnLayout', 'savedLayout'))).toHaveLength(1)
    // A second component reusing the name through other kinds of binding.
    expect(problems(`${good}${component('const columnLayout = gridPrefs.columnLayout ?? undefined\n', 'columnLayout', 'columnLayout', 'Other')}`)).toHaveLength(2)
    expect(problems(`${good}${component('let columnLayout = gridPrefs.columnLayout ?? defaultColumnLayout(r)\n', 'columnLayout', 'columnLayout', 'Other')}`)).toHaveLength(2)
    expect(problems(`${good}${component('const { columnLayout } = gridPrefs\n', 'columnLayout', 'columnLayout', 'Other')}`)).toHaveLength(2)
    expect(problems(`${good}export function Other({ columnLayout }: P) {\n  return (<>${panel('columnLayout')}${grid('columnLayout')}</>)\n}\n`)).toHaveLength(2)
    // An old fallback left in a comment is not a declaration.
    expect(problems(component(`// ${memo.replace(/\n/g, ' ')}\nconst { columnLayout } = gridPrefs\n`, 'columnLayout', 'columnLayout'))).toHaveLength(2)
    // A conditional is not the fallback.
    expect(problems(component('const columnLayout = useMemo(() => (compact ? saved : saved ?? defaultColumnLayout(r)), [x])\n', 'columnLayout', 'columnLayout'))).toHaveLength(2)
    // No layout on the grid at all, or no grid.
    expect(problems(`export function G() {\n${memo}  return (<>${panel('columnLayout')}<DataGrid rows={rows} /></>)\n}\n`)).toHaveLength(1)
    expect(problems(`export function G() {\n${memo}  return ${panel('columnLayout')}\n}\n`)).toHaveLength(1)
    // A file without a Columns panel is not this rule's business.
    expect(problems('export const G = () => <DataGrid columnLayout={gridPrefs.columnLayout ?? undefined} />')).toEqual([])
  })
})
