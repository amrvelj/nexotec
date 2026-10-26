/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { conditionsForField, type FilterFieldDef, type FilterPredicate } from '@nexotec/ui-kit'
import { applyStockFilters, buildStockFilterFields } from '../stockOptions'
import { applyCustomerFilters } from '../customerOptions'
import { applyCatalogueFilters, buildCatalogueFilterFields, CATALOGUE_FILTER_SCOPE, FACET_LIST_CODE } from '../catalogueOptions'
import type { CatalogueFacetsRead } from '../api/types'

// KAN-458: the Stock grid offered «Zustand = Neu», showed it as an active
// filter, and never sent it — the list still held used cars. A grid may
// offer only a predicate its list endpoint applies (UI/UX spec U-09). Two
// checks:
//
// 1. Behaviour: for every grid, every offered field × condition is run
//    through the grid's own predicate-to-params mapper with a sample value,
//    and must set a query parameter. The mapper is executed, not read, so a
//    comment cannot satisfy it. Customers' fields are its columns'
//    `meta.filter` declarations, read from the page with the TypeScript
//    compiler (they are built inside the component).
// 2. Coverage: every file that renders `<ViewsAndFilters fields={…}>` with
//    anything but an empty list must be one of the grids check 1 runs, and
//    must call its mapper and `offeredPredicates` (so a predicate from the
//    URL or a saved view that the grid does not offer is neither sent nor
//    shown). A new filterable grid fails here until it is added.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

type Mapper = (params: URLSearchParams, predicates: FilterPredicate[]) => void

/** Grid file (relative to src) → the mapper it must call. */
const COVERED_GRIDS: Record<string, string> = {
  'pages/StockListPage.tsx': 'applyStockFilters',
  'pages/CustomersListPage.tsx': 'applyCustomerFilters',
  'components/catalogue/CatalogueBrowseGrid.tsx': 'applyCatalogueFilters',
}

function samplePredicate(field: FilterFieldDef, condition: string): FilterPredicate {
  const base = { id: `${field.id}:${condition}`, fieldId: field.id, type: field.type, condition }
  switch (field.type) {
    case 'select':
      return { ...base, value: field.options?.[0]?.value ?? 'sample' }
    case 'number':
      return { ...base, value: 5 }
    case 'date':
      return { ...base, days: 7 }
    case 'boolean':
      return { ...base, value: true }
    case 'text':
      return { ...base, value: 'sample' }
  }
}

/** Every offered `field:condition` the mapper sends nothing for. */
function unmapped(fields: FilterFieldDef[], apply: Mapper): string[] {
  const missing: string[] = []
  for (const field of fields) {
    for (const condition of conditionsForField(field)) {
      const params = new URLSearchParams()
      apply(params, [samplePredicate(field, condition)])
      if ([...params.keys()].length === 0) missing.push(`${field.id}:${condition}`)
    }
  }
  return missing
}

const t = (key: string) => key

function parse(fileName: string, text: string): ts.SourceFile {
  return ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
}

function walk(node: ts.Node, visit: (n: ts.Node) => void): void {
  visit(node)
  node.forEachChild((child) => walk(child, visit))
}

function stringProperty(literal: ts.ObjectLiteralExpression, name: string): string | undefined {
  for (const p of literal.properties) {
    if (ts.isPropertyAssignment(p) && p.name.getText() === name && ts.isStringLiteralLike(p.initializer)) {
      return p.initializer.text
    }
  }
  return undefined
}

/** The `filter: { type, param, conditions }` literals of a page's columns, as
 * FilterFieldDefs (options are not needed to judge the mapping). A literal
 * without a literal `type`, `param` or `conditions` array is reported. */
function columnFilterFields(fileName: string, text: string): { fields: FilterFieldDef[]; problems: string[] } {
  const fields: FilterFieldDef[] = []
  const problems: string[] = []
  walk(parse(fileName, text), (node) => {
    if (!ts.isPropertyAssignment(node) || node.name.getText() !== 'filter' || !ts.isObjectLiteralExpression(node.initializer)) return
    const literal = node.initializer
    const line = literal.getSourceFile().getLineAndCharacterOfPosition(literal.getStart()).line + 1
    const type = stringProperty(literal, 'type') as FilterFieldDef['type'] | undefined
    const param = stringProperty(literal, 'param')
    const conditionsProp = literal.properties.find(
      (p): p is ts.PropertyAssignment => ts.isPropertyAssignment(p) && p.name.getText() === 'conditions',
    )
    const conditions =
      conditionsProp && ts.isArrayLiteralExpression(conditionsProp.initializer)
        ? conditionsProp.initializer.elements.map((e) => (ts.isStringLiteralLike(e) ? e.text : null))
        : null
    if (!type || !param || !conditions || conditions.includes(null)) {
      problems.push(`${fileName}:${line} — a column filter needs a literal type, param and conditions list`)
      return
    }
    fields.push({ id: param, label: param, type, param, conditions: conditions as string[] })
  })
  return { fields, problems }
}

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (/\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry)) out.push(full)
  }
  return out
}

function calls(file: ts.SourceFile, name: string): boolean {
  let found = false
  walk(file, (node) => {
    if (ts.isCallExpression(node) && node.expression.getText() === name) found = true
  })
  return found
}

/** Problems with one file's filter controls; `covered` names its mapper. */
function coverageProblems(fileName: string, text: string, covered: Record<string, string>): string[] {
  if (!/<ViewsAndFilters\b/.test(text)) return []
  const file = parse(fileName, text)
  let offersFields = false
  walk(file, (node) => {
    if (!(ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) || node.tagName.getText() !== 'ViewsAndFilters') return
    for (const attr of node.attributes.properties) {
      if (!ts.isJsxAttribute(attr) || attr.name.getText() !== 'fields') continue
      const expr = attr.initializer && ts.isJsxExpression(attr.initializer) ? attr.initializer.expression : undefined
      if (!(expr && ts.isArrayLiteralExpression(expr) && expr.elements.length === 0)) offersFields = true
    }
  })
  if (!offersFields) return []
  const mapper = covered[fileName]
  if (!mapper) return [`${fileName} offers filter fields but is not covered by grid-filters-are-applied.test.ts`]
  const out: string[] = []
  if (!calls(file, mapper)) out.push(`${fileName} does not call ${mapper}`)
  if (!calls(file, 'offeredPredicates')) out.push(`${fileName} does not narrow its predicates with offeredPredicates`)
  return out
}

describe('every offered filter is applied server-side (KAN-458)', () => {
  it('Stock: every offered field and condition is sent', () => {
    const fields = buildStockFilterFields(t)
    expect(fields.map((f) => f.id)).toEqual(['lifecycleStatus', 'condition'])
    expect(unmapped(fields, applyStockFilters)).toEqual([])
  })

  it('Customers: every column filter declares its param and conditions, and every one is sent', () => {
    const { fields, problems } = columnFilterFields(
      'pages/CustomersListPage.tsx',
      readFileSync(join(SRC_ROOT, 'pages/CustomersListPage.tsx'), 'utf8'),
    )
    expect(problems).toEqual([])
    expect(fields.length).toBeGreaterThan(0)
    expect(unmapped(fields, (params, predicates) => applyCustomerFilters(params, predicates, fields, new Date()))).toEqual([])
  })

  it('Catalogue: every offered field and condition is sent, and the URL scope matches what is offered', () => {
    const facets = {
      coded: Object.fromEntries(Object.keys(FACET_LIST_CODE).map((f) => [f, [{ valueCode: 'x', count: 1 }]])),
      numeric: Object.fromEntries(
        CATALOGUE_FILTER_SCOPE.filter((f) => f.type === 'number').map((f) => [f.id, { min: 0, max: 10 }]),
      ),
    } as unknown as CatalogueFacetsRead
    const fields = buildCatalogueFilterFields(t, facets, (_l, v) => v)
    expect(unmapped(fields, applyCatalogueFilters)).toEqual([])
    const scope = (list: FilterFieldDef[]) => list.map((f) => [f.id, f.type, conditionsForField(f)])
    expect(scope(fields)).toEqual(scope(CATALOGUE_FILTER_SCOPE))
  })

  it('every grid that offers filter fields is covered, calls its mapper and narrows with offeredPredicates', () => {
    const found = collectFiles(SRC_ROOT).flatMap((full) =>
      coverageProblems(relative(SRC_ROOT, full).split('\\').join('/'), readFileSync(full, 'utf8'), COVERED_GRIDS),
    )
    expect(found).toEqual([])
  })
})

describe('the guard itself', () => {
  it('flags an offered condition the mapper drops — the original Stock defect', () => {
    const fields: FilterFieldDef[] = [
      { id: 'condition', label: 'Zustand', type: 'select', options: [{ value: 'new', label: 'Neu' }] },
    ]
    const sendsNothing: Mapper = () => {}
    expect(unmapped(fields, sendsNothing)).toEqual(['condition:is', 'condition:isNot'])
  })

  it('flags an uncovered grid, and is not satisfied by a comment naming the calls', () => {
    const page = `
      // applyStockFilters(params, predicates); offeredPredicates(p, f)
      export function P() { return <ViewsAndFilters fields={fields} predicates={predicates} /> }`
    expect(coverageProblems('pages/New.tsx', page, {})).toEqual([
      'pages/New.tsx offers filter fields but is not covered by grid-filters-are-applied.test.ts',
    ])
    expect(coverageProblems('pages/New.tsx', page, { 'pages/New.tsx': 'applyStockFilters' })).toEqual([
      'pages/New.tsx does not call applyStockFilters',
      'pages/New.tsx does not narrow its predicates with offeredPredicates',
    ])
  })

  it('leaves a grid with an empty field list alone', () => {
    expect(coverageProblems('pages/X.tsx', 'const x = <ViewsAndFilters fields={[]} predicates={[]} />', {})).toEqual([])
  })

  it('reports a column filter without a conditions list', () => {
    const { problems } = columnFilterFields('p.tsx', "const c = { meta: { filter: { type: 'select', param: 'canton' } } }")
    expect(problems).toEqual(['p.tsx:1 — a column filter needs a literal type, param and conditions list'])
  })
})
