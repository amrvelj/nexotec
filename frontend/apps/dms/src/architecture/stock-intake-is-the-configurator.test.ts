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
// The API now refuses a create without `configurationId`; this keeps a screen
// from building one again: every post to the create endpoint types its body
// as `StockItemCreateRequest`, whose `configurationId` the generated schema
// makes required, so `tsc` refuses a body without it.

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'architecture' || name === 'test' ? [] : sourceFiles(path)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== 'schema.d.ts' ? [path] : []
  })
}

// `api.post<…>('/inventory/stock-items', body, …)` — the collection itself,
// not a sub-resource (`/inventory/stock-items/${id}/…`).
const CREATE_POST = /api\.post(?:<[^>]*>)?\(\s*['"`]\/inventory\/stock-items['"`]\s*,\s*(\w+)/g

describe('the stock intake is the configurator', () => {
  const posts = sourceFiles(SRC).flatMap((path) => {
    const source = readFileSync(path, 'utf-8')
    return [...source.matchAll(CREATE_POST)].map((m) => ({ file: relative(SRC, path), source, body: m[1] }))
  })

  it('posts a stock item from the configurator intake alone', () => {
    expect(posts.map((p) => p.file)).toEqual(['pages/stock/AddToPipelineButton.tsx'])
  })

  it('types every create body as the request that requires a configuration', () => {
    for (const { file, source, body } of posts) {
      expect(source, `${file}: ${body}`).toMatch(new RegExp(`const ${body}: StockItemCreateRequest = \\{`))
      expect(source, `${file}: ${body}`).toMatch(/configurationId: configuration\.id/)
    }
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
