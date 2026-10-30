/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// KAN-468: the customer screen's primary «Edit» was wired to
// `onEdit: () => setActiveTab('overview')` — on the Overview tab, where the
// button is used, it did nothing, and the most prominent button on the
// screen taught users not to trust the primary action. ADR-061 gives every
// detail screen one primary; this guard refuses a primary whose whole effect
// is selecting a tab (a tab switch is a no-op on that tab).
//
// What it reads, with the TypeScript compiler:
// - every `build…RowMenu` builder: the handler names its `primary` can come
//   from (`primary: newOffer ?? linkVehicle`, each an object whose `onClick`
//   is `handlers.onX`);
// - every file that renders `<DetailHeader`: the arrow passed for each of
//   those handlers to the builder, and an inline `primaryAction={{ onClick }}`.
// An arrow whose body is one call of `set…Tab(…)` is refused.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

interface Source {
  path: string
  text: string
}

function collectSources(dir: string, out: Source[] = []): Source[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectSources(full, out)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      out.push({ path: relative(SRC_ROOT, full), text: readFileSync(full, 'utf8') })
    }
  }
  return out
}

function parse(source: Source): ts.SourceFile {
  return ts.createSourceFile(source.path, source.text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
}

function walk(node: ts.Node, visit: (n: ts.Node) => void): void {
  visit(node)
  node.forEachChild((child) => walk(child, visit))
}

function propertyName(node: ts.ObjectLiteralElementLike): string | undefined {
  return node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) ? node.name.text : undefined
}

/** `() => setActiveTab('x')` or `() => { setActiveTab('x') }`. */
export function isPureTabSwitch(node: ts.Node): boolean {
  if (!ts.isArrowFunction(node) && !ts.isFunctionExpression(node)) return false
  let body: ts.Node = node.body
  if (ts.isBlock(body)) {
    if (body.statements.length !== 1) return false
    const only = body.statements[0]
    if (!ts.isExpressionStatement(only)) return false
    body = only.expression
  }
  if (ts.isVoidExpression(body)) body = body.expression
  return ts.isCallExpression(body) && ts.isIdentifier(body.expression) && /^set\w*Tab$/.test(body.expression.text)
}

/** Builder name → the handler names its `primary` can come from. */
export function primaryHandlersOfBuilders(sources: Source[]): Map<string, Set<string>> {
  const result = new Map<string, Set<string>>()
  for (const source of sources) {
    const file = parse(source)
    walk(file, (node) => {
      if (!ts.isFunctionDeclaration(node) || !node.name || !/^build\w*RowMenu$/.test(node.name.text) || !node.body) return
      const locals = new Map<string, ts.Expression>()
      const handlers = new Set<string>()
      let primary: ts.Expression | undefined
      walk(node.body, (inner) => {
        if (ts.isVariableDeclaration(inner) && ts.isIdentifier(inner.name) && inner.initializer) {
          locals.set(inner.name.text, inner.initializer)
        }
        if (ts.isReturnStatement(inner) && inner.expression && ts.isObjectLiteralExpression(inner.expression)) {
          for (const prop of inner.expression.properties) {
            if (ts.isPropertyAssignment(prop) && propertyName(prop) === 'primary') primary = prop.initializer
          }
        }
      })
      const seen = new Set<ts.Node>()
      const collect = (expr: ts.Node) => {
        if (seen.has(expr)) return
        seen.add(expr)
        walk(expr, (n) => {
          if (ts.isIdentifier(n) && locals.has(n.text)) collect(locals.get(n.text)!)
          if (ts.isPropertyAccessExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'handlers') {
            handlers.add(n.name.text)
          }
        })
      }
      if (primary) collect(primary)
      result.set(node.name.text, handlers)
    })
  }
  return result
}

/** Every primary-action handler in a `<DetailHeader>` file that only selects a tab. */
export function noOpPrimaries(pages: Source[], builders: Map<string, Set<string>>): string[] {
  const offences: string[] = []
  for (const source of pages) {
    if (!source.text.includes('<DetailHeader')) continue
    const file = parse(source)
    const where = (n: ts.Node) => `${source.path}:${file.getLineAndCharacterOfPosition(n.getStart()).line + 1}`
    walk(file, (node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && builders.has(node.expression.text)) {
        const primaryHandlers = builders.get(node.expression.text)!
        for (const arg of node.arguments) {
          if (!ts.isObjectLiteralExpression(arg)) continue
          for (const prop of arg.properties) {
            const name = propertyName(prop)
            if (!name || !primaryHandlers.has(name) || !ts.isPropertyAssignment(prop)) continue
            let value: ts.Node = prop.initializer
            // `salesEntitled ? () => … : undefined`
            if (ts.isConditionalExpression(value)) value = value.whenTrue
            if (isPureTabSwitch(value)) offences.push(`${where(prop)} ${name} (primary of ${node.expression.text})`)
          }
        }
      }
      if (ts.isJsxAttribute(node) && node.name.getText() === 'primaryAction' && node.initializer) {
        walk(node.initializer, (n) => {
          if (ts.isPropertyAssignment(n) && propertyName(n) === 'onClick' && isPureTabSwitch(n.initializer)) {
            offences.push(`${where(n)} primaryAction.onClick`)
          }
        })
      }
    })
  }
  return offences
}

describe('a detail header primary is never a tab switch (KAN-468, ADR-061)', () => {
  it('no screen wires its primary to a handler that only selects a tab', () => {
    const sources = collectSources(SRC_ROOT)
    const builders = primaryHandlersOfBuilders(sources)
    expect([...builders.keys()]).toEqual(expect.arrayContaining(['buildCustomerRowMenu', 'buildVehicleRowMenu']))
    expect(noOpPrimaries(sources, builders)).toEqual([])
  })

  it('reads the customer and vehicle primaries from their builders', () => {
    const builders = primaryHandlersOfBuilders(collectSources(SRC_ROOT))
    expect([...builders.get('buildCustomerRowMenu')!].sort()).toEqual(['onLinkVehicle', 'onNewOffer'])
    expect([...builders.get('buildVehicleRowMenu')!]).toEqual(['onAllocate'])
  })

  it('goes red for the shape KAN-468 removed, and for an inline primary', () => {
    const builder: Source = {
      path: 'components/oldRowMenu.tsx',
      text: `export function buildOldRowMenu(t, c, handlers) {
        const edit = { label: t('edit'), onClick: handlers.onEdit }
        return { primary: edit, overflow: {} }
      }`,
    }
    const page: Source = {
      path: 'pages/OldDetailPage.tsx',
      text: `export function OldDetailPage() {
        const rowMenu = buildOldRowMenu(t, customer, { onEdit: () => setActiveTab('overview'), onCopy: () => {} })
        return <DetailHeader primaryAction={rowMenu.primary} />
      }
      export function Inline() {
        return <DetailHeader primaryAction={{ label: 'x', onClick: () => { setActiveTab('identity') } }} />
      }`,
    }
    const builders = primaryHandlersOfBuilders([builder])
    expect(noOpPrimaries([page], builders)).toEqual([
      'pages/OldDetailPage.tsx:2 onEdit (primary of buildOldRowMenu)',
      'pages/OldDetailPage.tsx:6 primaryAction.onClick',
    ])

    const fixed: Source = {
      ...page,
      text: page.text
        .replace("() => setActiveTab('overview')", "() => { setActiveTab('identity'); setAllocating(true) }")
        .replace("() => { setActiveTab('identity') }", '() => navigate(`/x`)'),
    }
    expect(noOpPrimaries([fixed], builders)).toEqual([])
  })
})
