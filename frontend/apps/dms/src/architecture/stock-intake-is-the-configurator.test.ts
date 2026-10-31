/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-463 (Anto, 2026-10-26): every stock vehicle enters through the
// configurator. The hand-typed «Fahrzeug aufnehmen» form posted a label and
// a condition, and the car it created had no vehicle and no technical data.
// The API refuses a create without `configurationId` (422, the real guard);
// this keeps a screen from building one again: only the configurator intake
// names the create path, and it types its body as `StockItemCreateRequest`,
// whose `configurationId` the generated schema makes required.

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'architecture' || name === 'test' ? [] : sourceFiles(path)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== 'schema.d.ts' ? [path] : []
  })
}

// Comments are not code: the scan reads what the file executes.
const withoutComments = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

// The create endpoint's path as a whole string literal — however it is then
// used (passed inline, kept in a constant, wrapped in another helper). A
// read of the list carries a query string (`/inventory/stock-items?…`) and
// a sub-resource a further segment, so neither is this literal.
const CREATE_PATH_LITERAL = /(['"`])\/inventory\/stock-items\1/
// A POST to the collection with a query string would still be a create.
const CREATE_POST_WITH_QUERY = /\.post(?:<[^(]*>)?\(\s*[`'"]\/inventory\/stock-items\?/
// KAN-511 — the spellings the first scan missed (the server's 422 stays the
// real guard): a trailing slash, an interpolation straight after the
// collection (`…/stock-items${x}`; a sub-resource has its slash first), and
// the path built by concatenation from an `/inventory/` literal.
const CREATE_PATH_EVASIONS = [
  /(['"`])\/inventory\/stock-items\/\1/,
  /`\/inventory\/stock-items\$\{/,
  /(['"`])\/inventory\/?\1\s*\+/,
]

describe('the stock intake is the configurator', () => {
  const files = sourceFiles(SRC).map((path) => ({ file: relative(SRC, path), source: withoutComments(readFileSync(path, 'utf-8')) }))

  it('names the create path in the configurator intake alone', () => {
    expect(files.filter((f) => CREATE_PATH_LITERAL.test(f.source)).map((f) => f.file)).toEqual([
      'pages/stock/AddToPipelineButton.tsx',
    ])
    expect(files.filter((f) => CREATE_POST_WITH_QUERY.test(f.source)).map((f) => f.file)).toEqual([])
  })

  it('refuses the create path spelt another way', () => {
    for (const pattern of CREATE_PATH_EVASIONS) {
      expect(files.filter((f) => pattern.test(f.source)).map((f) => f.file), String(pattern)).toEqual([])
    }
  })

  it('would see each of those spellings', () => {
    const spellings = [
      "api.post('/inventory/stock-items/', body)",
      'api.post(`/inventory/stock-items${suffix}`, body)',
      "api.post('/inventory/' + 'stock-items', body)",
      "api.post('/inventory' + '/stock-items', body)",
    ]
    for (const spelling of spellings) {
      expect(CREATE_PATH_EVASIONS.some((pattern) => pattern.test(spelling)), spelling).toBe(true)
    }
    // A sub-resource and the list read are not a create.
    for (const legit of ['api.post(`/inventory/stock-items/${id}/configuration`, body)', "api.get('/inventory/stock-items?q=golf')"]) {
      expect(CREATE_PATH_EVASIONS.some((pattern) => pattern.test(legit)), legit).toBe(false)
    }
  })

  it('types the create body as the request that requires a configuration', () => {
    const intake = files.find((f) => f.file === 'pages/stock/AddToPipelineButton.tsx')!.source
    expect(intake).toMatch(/api\.post<StockItemRead>\('\/inventory\/stock-items', body,/)
    expect(intake).toMatch(/const body: StockItemCreateRequest = \{/)
    expect(intake).toMatch(/configurationId: configuration\.id/)
  })

  it('the generated schema makes configurationId required on the create', () => {
    const schema = readFileSync(join(SRC, 'api/schema.d.ts'), 'utf-8')
    const request = schema.match(/StockItemCreateRequest: \{([\s\S]*?)\n {8}\};/)
    expect(request, 'StockItemCreateRequest in schema.d.ts').not.toBeNull()
    expect(request![1]).toMatch(/\n\s+configurationId: string;/)
  })

  it('has no hand-typed intake route left', () => {
    const app = readFileSync(join(SRC, 'App.tsx'), 'utf-8')
    expect(app).not.toMatch(/\/stock\/new|StockCreatePage/)
  })
})
