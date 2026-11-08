/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { conditionsForField, type FilterFieldDef, type FilterPredicate } from '@nexotec/ui-kit'
import { applyGroupStockFilters, applyStockFilters, buildGroupStockFilterFields, buildStockFilterFields } from '../stockOptions'
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
//    and must set a query parameter that its list endpoint declares (read
//    from the generated `schema.d.ts` — FastAPI ignores an unknown one, which
//    is how the original defect would hide one layer down). The mapper is
//    executed, not read, so a comment cannot satisfy it. Customers' fields
//    are its columns' `meta.filter` declarations, read from the page with the
//    TypeScript compiler (they are built inside the component).
// 2. Shape: no mapper contains a `continue` — the statement the original
//    mappers dropped an offered predicate with.
// 3. Coverage: every file that renders a filter control (`ViewsAndFilters`,
//    `FilterBuilder`, `FilterChips`) with anything but an empty field list
//    must be one of the grids check 1 runs, and must call its mapper and
//    `offeredPredicates` (so a predicate from the URL or a saved view that
//    the grid cannot send is neither sent nor shown). A new filterable grid
//    fails here until it is added.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

type Mapper = (params: URLSearchParams, predicates: FilterPredicate[]) => void

/** Grid file (relative to src) → the mapper it must call. */
const COVERED_GRIDS: Record<string, string> = {
  'pages/StockListPage.tsx': 'applyStockFilters',
  'pages/stock/GroupStockGrid.tsx': 'applyGroupStockFilters',
  'pages/CustomersListPage.tsx': 'applyCustomerFilters',
  'components/catalogue/CatalogueBrowseGrid.tsx': 'applyCatalogueFilters',
}

/** Mapper → the module that defines it and the list endpoint it feeds. */
const MAPPERS: Record<string, { module: string; endpoint: string }> = {
  applyStockFilters: { module: 'stockOptions.ts', endpoint: '/v1/inventory/stock-items' },
  applyGroupStockFilters: { module: 'stockOptions.ts', endpoint: '/v1/inventory/groups/mine/stock-items' },
  applyCustomerFilters: { module: 'customerOptions.ts', endpoint: '/v1/customers' },
  applyCatalogueFilters: { module: 'catalogueOptions.ts', endpoint: '/v1/catalogue/variants' },
}

const SCHEMA = readFileSync(join(SRC_ROOT, 'api/schema.d.ts'), 'utf8')

/** The query parameter names `schema.d.ts` declares for `GET <path>`. */
function queryParamsOf(path: string): Set<string> {
  const pathAt = SCHEMA.indexOf(`    "${path}": {`)
  const operation = /get: operations\["([^"]+)"\]/.exec(SCHEMA.slice(pathAt))?.[1]
  const opAt = SCHEMA.indexOf(`\n    ${operation}: {`)
  const queryAt = SCHEMA.indexOf('query?: {', opAt)
  const block = SCHEMA.slice(queryAt, SCHEMA.indexOf('\n            };', queryAt))
  return new Set([...block.matchAll(/^\s+"?([A-Za-z_][\w]*)"?\??:/gm)].map((m) => m[1]))
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

/** Every offered `field:condition` the mapper sends nothing for, or sends
 * under a parameter the endpoint does not declare. */
function unmapped(fields: FilterFieldDef[], apply: Mapper, declared?: Set<string>): string[] {
  const missing: string[] = []
  for (const field of fields) {
    for (const condition of conditionsForField(field)) {
      const params = new URLSearchParams()
      apply(params, [samplePredicate(field, condition)])
      const sent = [...params.keys()]
      if (sent.length === 0) missing.push(`${field.id}:${condition}`)
      for (const name of sent) {
        if (declared && !declared.has(name)) missing.push(`${field.id}:${condition} sends undeclared ${name}`)
      }
    }
  }
  return missing
}

/** `continue` statements inside the named function of a module. */
function continuesIn(fileName: string, text: string, functionName: string): string[] {
  const file = parse(fileName, text)
  const found: string[] = []
  let seen = false
  walk(file, (node) => {
    if (!ts.isFunctionDeclaration(node) || node.name?.text !== functionName) return
    seen = true
    walk(node, (inner) => {
      if (ts.isContinueStatement(inner)) {
        const line = file.getLineAndCharacterOfPosition(inner.getStart()).line + 1
        found.push(`${fileName}:${line} — ${functionName} drops a predicate with continue`)
      }
    })
  })
  return seen ? found : [`${fileName} defines no ${functionName}`]
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

/** The columns' `meta: { filter: { type, param, conditions } }` literals of
 * a page, as FilterFieldDefs keyed by the column's own `id` — the id a
 * predicate stores (options are not needed to judge the mapping). A filter
 * that is not such a literal, or a column without a literal `id`, is
 * reported rather than skipped. */
function columnFilterFields(fileName: string, text: string): { fields: FilterFieldDef[]; problems: string[] } {
  const fields: FilterFieldDef[] = []
  const problems: string[] = []
  const file = parse(fileName, text)
  walk(file, (node) => {
    const isFilter = (ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node)) && node.name.getText() === 'filter'
    if (!isFilter) return
    const meta = node.parent.parent
    if (!ts.isPropertyAssignment(meta) || meta.name.getText() !== 'meta') return
    const line = file.getLineAndCharacterOfPosition(node.getStart()).line + 1
    const column = meta.parent
    const id = ts.isObjectLiteralExpression(column) ? stringProperty(column, 'id') : undefined
    const literal = ts.isPropertyAssignment(node) ? node.initializer : undefined
    if (!literal || !ts.isObjectLiteralExpression(literal) || !id) {
      problems.push(`${fileName}:${line} — a column filter must be an object literal on a column with a literal id`)
      return
    }
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
    fields.push({ id, label: id, type, param, conditions: conditions as string[] })
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

const FILTER_CONTROLS = new Set(['ViewsAndFilters', 'FilterBuilder', 'FilterChips'])

/** Problems with one file's filter controls; `covered` names its mapper. */
function coverageProblems(fileName: string, text: string, covered: Record<string, string>): string[] {
  if (!/<(ViewsAndFilters|FilterBuilder|FilterChips)\b/.test(text)) return []
  const file = parse(fileName, text)
  let offersFields = false
  walk(file, (node) => {
    if (!(ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node))) return
    if (!FILTER_CONTROLS.has(node.tagName.getText())) return
    for (const attr of node.attributes.properties) {
      // A spread may carry `fields`; it is judged as offering them.
      if (ts.isJsxSpreadAttribute(attr)) offersFields = true
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
    expect(unmapped(fields, applyStockFilters, queryParamsOf(MAPPERS.applyStockFilters.endpoint))).toEqual([])
  })

  it('Group stock: the Betrieb filter, «ist» and «ist nicht», is sent (KAN-565)', () => {
    const fields = buildGroupStockFilterFields(t, [{ id: 'd-1', label: 'Garage Nord AG' }])
    expect(fields.map((f) => f.id)).toEqual(['dealershipId'])
    expect(
      unmapped(fields, applyGroupStockFilters, queryParamsOf(MAPPERS.applyGroupStockFilters.endpoint)),
    ).toEqual([])
  })

  it('Customers: every column filter declares its param and conditions, and every one is sent', () => {
    const { fields, problems } = columnFilterFields(
      'pages/CustomersListPage.tsx',
      readFileSync(join(SRC_ROOT, 'pages/CustomersListPage.tsx'), 'utf8'),
    )
    expect(problems).toEqual([])
    expect(fields.length).toBeGreaterThan(0)
    const apply: Mapper = (params, predicates) => applyCustomerFilters(params, predicates, fields)
    expect(unmapped(fields, apply, queryParamsOf(MAPPERS.applyCustomerFilters.endpoint))).toEqual([])
  })

  it('Catalogue: every offered field and condition is sent, and the URL scope matches what is offered', () => {
    const facets = {
      coded: Object.fromEntries(Object.keys(FACET_LIST_CODE).map((f) => [f, [{ valueCode: 'x', count: 1 }]])),
      numeric: Object.fromEntries(
        CATALOGUE_FILTER_SCOPE.filter((f) => f.type === 'number').map((f) => [f.id, { min: 0, max: 10 }]),
      ),
    } as unknown as CatalogueFacetsRead
    const fields = buildCatalogueFilterFields(t, facets, (_l, v) => v)
    expect(unmapped(fields, applyCatalogueFilters, queryParamsOf(MAPPERS.applyCatalogueFilters.endpoint))).toEqual([])
    const scope = (list: FilterFieldDef[]) => list.map((f) => [f.id, f.type, conditionsForField(f)])
    expect(scope(fields)).toEqual(scope(CATALOGUE_FILTER_SCOPE))
  })

  it('reads the endpoints\' declared query parameters from schema.d.ts', () => {
    expect(queryParamsOf('/v1/inventory/stock-items')).toEqual(
      new Set(['q', 'lifecycle_status', 'condition', 'lifecycle_status_not', 'condition_not', 'sort', 'limit', 'cursor']),
    )
    // KAN-476: the exclusions «ist nicht» is sent as.
    expect(queryParamsOf('/v1/customers')).toContain('advisorUserIdNot')
    expect(queryParamsOf('/v1/customers')).toContain('updated_more_than_days_ago')
    // KAN-548: the catalogue filters on the identAPI's filterable fields — kW, never PS or an exclusion.
    expect(queryParamsOf('/v1/catalogue/variants')).toContain('kwMin')
    expect(queryParamsOf('/v1/catalogue/variants')).not.toContain('psMin')
    expect(queryParamsOf('/v1/catalogue/variants')).not.toContain('fuelTypeNot')
  })

  it('no mapper drops a predicate with continue', () => {
    const found = Object.entries(MAPPERS).flatMap(([name, { module }]) =>
      continuesIn(module, readFileSync(join(SRC_ROOT, module), 'utf8'), name),
    )
    expect(found).toEqual([])
  })

  it('every covered grid names a mapper whose behaviour this file runs above', () => {
    // A new grid is covered by adding its mapper to MAPPERS and a behaviour
    // case above — never by a COVERED_GRIDS entry alone.
    const BEHAVIOUR_TESTED = ['applyStockFilters', 'applyGroupStockFilters', 'applyCustomerFilters', 'applyCatalogueFilters']
    expect(Object.keys(MAPPERS).sort()).toEqual([...BEHAVIOUR_TESTED].sort())
    expect([...new Set(Object.values(COVERED_GRIDS))].sort()).toEqual([...BEHAVIOUR_TESTED].sort())
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

  it('flags a parameter the endpoint does not declare — the original defect one layer down', () => {
    const fields: FilterFieldDef[] = [{ id: 'fuelType', label: 'Fuel', type: 'select', conditions: ['is'] }]
    const sendsUnknown: Mapper = (params) => params.set('fuel_type', 'petrol')
    expect(unmapped(fields, sendsUnknown, queryParamsOf('/v1/inventory/stock-items'))).toEqual([
      'fuelType:is sends undeclared fuel_type',
    ])
  })

  it('flags a mapper that drops a predicate with continue', () => {
    const mapper = `export function applyX(params, predicates) {
      for (const p of predicates) { if (p.condition === 'isNot') continue; params.set(p.fieldId, p.value) }
    }`
    expect(continuesIn('x.ts', mapper, 'applyX')).toEqual(['x.ts:2 — applyX drops a predicate with continue'])
  })

  it('finds a FilterBuilder rendered directly, not only ViewsAndFilters', () => {
    expect(coverageProblems('pages/Y.tsx', 'const y = <FilterBuilder fields={fields} />', {})).toEqual([
      'pages/Y.tsx offers filter fields but is not covered by grid-filters-are-applied.test.ts',
    ])
  })

  it('judges a filter control fed by a spread as offering fields', () => {
    expect(coverageProblems('pages/Z.tsx', 'const z = <ViewsAndFilters {...filterProps} />', {})).toEqual([
      'pages/Z.tsx offers filter fields but is not covered by grid-filters-are-applied.test.ts',
    ])
  })

  it('reports a shorthand column filter rather than skipping it', () => {
    const { problems } = columnFilterFields('p.tsx', "const c = { id: 'canton', meta: { filter } }")
    expect(problems).toEqual(['p.tsx:1 — a column filter must be an object literal on a column with a literal id'])
  })

  it('leaves a grid with an empty field list alone', () => {
    expect(coverageProblems('pages/X.tsx', 'const x = <ViewsAndFilters fields={[]} predicates={[]} />', {})).toEqual([])
  })

  it('reports a column filter without a conditions list, and one that is not a literal', () => {
    const missing = columnFilterFields('p.tsx', "const c = { id: 'canton', meta: { filter: { type: 'select', param: 'canton' } } }")
    expect(missing.problems).toEqual(['p.tsx:1 — a column filter needs a literal type, param and conditions list'])
    const built = columnFilterFields('p.tsx', "const c = { id: 'canton', meta: { filter: cantonFilter } }")
    expect(built.problems).toEqual(['p.tsx:1 — a column filter must be an object literal on a column with a literal id'])
  })

  it('keys a column filter by the column id a predicate stores, not by its param', () => {
    const { fields } = columnFilterFields(
      'p.tsx',
      "const c = { id: 'updatedAt', meta: { filter: { type: 'date', param: 'updated_since', conditions: ['today'] } } }",
    )
    expect(fields.map((f) => [f.id, f.param])).toEqual([['updatedAt', 'updated_since']])
  })
})
