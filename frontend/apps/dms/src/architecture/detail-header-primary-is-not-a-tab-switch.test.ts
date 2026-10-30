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
//   is `<handlers parameter>.onX`);
// - every file that renders `<DetailHeader`: the value passed for each of
//   those handlers to the builder, and the `onClick` of an inline
//   `primaryAction={{ … }}`.
// A value is judged through the names it uses: a handler bound to a name
// (`const goOverview = () => setActiveTab('overview')`, `onEdit: goOverview`)
// and an arrow that only calls such a name are followed to their
// declaration in the file. Refused: a body that is one `set…Tab(…)` call or
// one `setSearchParams(…)` naming a tab — and a primary the scan cannot
// follow (a prop, an import, a member), so a new shape fails loudly instead
// of passing unjudged.

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

function unwrap(node: ts.Expression): ts.Expression {
  let current = node
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isSatisfiesExpression(current) ||
    ts.isNonNullExpression(current)
  ) {
    current = current.expression
  }
  return current
}

/** Every `const name = …` and `function name() {}` in the file, by name. */
function declarations(file: ts.SourceFile): Map<string, ts.Node[]> {
  const found = new Map<string, ts.Node[]>()
  const add = (name: string, node: ts.Node) => found.set(name, [...(found.get(name) ?? []), node])
  walk(file, (n) => {
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer) add(n.name.text, n.initializer)
    if (ts.isFunctionDeclaration(n) && n.name) add(n.name.text, n)
  })
  return found
}

/** The one expression a function's body consists of, if it is that simple. */
function soleExpression(fn: ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration): ts.Expression | null {
  const body = fn.body
  if (!body) return null
  if (!ts.isBlock(body)) return unwrap(body)
  if (body.statements.length !== 1) return null
  const only = body.statements[0]
  if (ts.isExpressionStatement(only)) return unwrap(only.expression)
  if (ts.isReturnStatement(only) && only.expression) return unwrap(only.expression)
  return null
}

function isTabSwitchCall(expr: ts.Expression): boolean {
  if (ts.isVoidExpression(expr)) expr = unwrap(expr.expression)
  if (!ts.isCallExpression(expr) || !ts.isIdentifier(expr.expression)) return false
  const callee = expr.expression.text
  if (/^set\w*Tab$/.test(callee)) return true
  return callee === 'setSearchParams' && expr.arguments.some((a) => /\btab\b/.test(a.getText()))
}

type Verdict = 'ok' | 'tab-switch' | 'unresolved'

/** Judges a handler value: `ok`, a pure tab switch, or something the scan cannot follow. */
function judge(value: ts.Expression, decls: Map<string, ts.Node[]>, seen = new Set<ts.Node>()): Verdict {
  const expr = unwrap(value)
  if (seen.has(expr)) return 'ok'
  seen.add(expr)
  if (ts.isConditionalExpression(expr)) {
    const sides = [expr.whenTrue, expr.whenFalse].filter((e) => unwrap(e).getText() !== 'undefined')
    const verdicts = sides.map((side) => judge(side, decls, seen))
    return verdicts.find((v) => v !== 'ok') ?? 'ok'
  }
  if (ts.isIdentifier(expr)) {
    const targets = decls.get(expr.text)
    if (!targets) return 'unresolved'
    const verdicts = targets.map((t) =>
      ts.isFunctionDeclaration(t) ? judgeFunction(t, decls, seen) : judge(t as ts.Expression, decls, seen),
    )
    return verdicts.find((v) => v !== 'ok') ?? 'ok'
  }
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return judgeFunction(expr, decls, seen)
  if (ts.isCallExpression(expr) && ts.isIdentifier(expr.expression) && expr.expression.text === 'useCallback') {
    return expr.arguments[0] ? judge(expr.arguments[0], decls, seen) : 'unresolved'
  }
  return 'unresolved'
}

function judgeFunction(
  fn: ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration,
  decls: Map<string, ts.Node[]>,
  seen: Set<ts.Node>,
): Verdict {
  const only = soleExpression(fn)
  if (!only) return 'ok'
  if (isTabSwitchCall(only)) return 'tab-switch'
  // `() => goOverview()`: follow the one local function it calls (a local
  // that is no function — `navigate` from `useNavigate()` — is not followed).
  const call = ts.isVoidExpression(only) ? unwrap(only.expression) : only
  if (ts.isCallExpression(call) && ts.isIdentifier(call.expression)) {
    const functions = (decls.get(call.expression.text) ?? []).filter(
      (d) => ts.isFunctionDeclaration(d) || ts.isArrowFunction(d) || ts.isFunctionExpression(d),
    )
    if (functions.length > 0) return judge(call.expression, decls, seen)
  }
  return 'ok'
}

/** Builder name → the handler names its `primary` can come from. */
export function primaryHandlersOfBuilders(sources: Source[]): Map<string, Set<string>> {
  const result = new Map<string, Set<string>>()
  for (const source of sources) {
    const file = parse(source)
    walk(file, (node) => {
      if (!ts.isFunctionDeclaration(node) || !node.name || !/^build\w*RowMenu$/.test(node.name.text) || !node.body) return
      const last = node.parameters[node.parameters.length - 1]
      const handlersParam = last && ts.isIdentifier(last.name) ? last.name.text : 'handlers'
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
          if (ts.isPropertyAccessExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === handlersParam) {
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

/** Every primary-action handler in a `<DetailHeader>` file that only selects a tab or cannot be followed. */
export function noOpPrimaries(pages: Source[], builders: Map<string, Set<string>>): string[] {
  const offences: string[] = []
  for (const source of pages) {
    if (!source.text.includes('<DetailHeader')) continue
    const file = parse(source)
    const decls = declarations(file)
    const where = (n: ts.Node) => `${source.path}:${file.getLineAndCharacterOfPosition(n.getStart()).line + 1}`
    const report = (n: ts.Node, what: string, verdict: Verdict) => {
      if (verdict !== 'ok') offences.push(`${where(n)} ${what}${verdict === 'unresolved' ? ' (cannot be followed)' : ''}`)
    }
    const builderResults = new Set<string>()
    walk(file, (node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && builders.has(node.expression.text)) {
        if (ts.isVariableDeclaration(node.parent) && ts.isIdentifier(node.parent.name)) builderResults.add(node.parent.name.text)
        const primaryHandlers = builders.get(node.expression.text)!
        for (const arg of node.arguments) {
          if (!ts.isObjectLiteralExpression(arg)) continue
          for (const prop of arg.properties) {
            const name = propertyName(prop)
            if (!name || !primaryHandlers.has(name)) continue
            const value = ts.isPropertyAssignment(prop)
              ? prop.initializer
              : ts.isShorthandPropertyAssignment(prop)
                ? prop.name
                : null
            report(prop, `${name} (primary of ${node.expression.text})`, value ? judge(value, decls) : 'unresolved')
          }
        }
      }
    })
    walk(file, (node) => {
      if (!ts.isJsxAttribute(node) || node.name.getText() !== 'primaryAction' || !node.initializer) return
      let onClicks = 0
      let fromBuilder = false
      walk(node.initializer, (n) => {
        if (ts.isPropertyAssignment(n) && propertyName(n) === 'onClick') {
          onClicks += 1
          report(n, 'primaryAction.onClick', judge(n.initializer, decls))
        }
        if (ts.isIdentifier(n) && builderResults.has(n.text)) fromBuilder = true
      })
      if (onClicks === 0 && !fromBuilder) report(node, 'primaryAction', 'unresolved')
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
    expect([...builders.get('buildValuationRowMenu')!]).toEqual(['onRevalue'])
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

    // The same defects written through a name, a forwarding arrow, the URL,
    // and a primary the scan cannot follow.
    const named: Source = {
      path: 'pages/NamedDetailPage.tsx',
      text: `export function NamedDetailPage({ onEditProp }) {
        const goOverview = () => setActiveTab('overview')
        function focusIdentity() { setSearchParams({ tab: 'identity' }) }
        const rowMenu = buildOldRowMenu(t, customer, { onEdit: goOverview })
        const other = buildOldRowMenu(t, customer, { onEdit: () => focusIdentity() })
        return <>
          <DetailHeader primaryAction={rowMenu.primary} />
          <DetailHeader primaryAction={{ label: 'x', onClick: onEditProp }} />
          <DetailHeader primaryAction={somethingElse} />
        </>
      }`,
    }
    expect(noOpPrimaries([named], builders)).toEqual([
      'pages/NamedDetailPage.tsx:4 onEdit (primary of buildOldRowMenu)',
      'pages/NamedDetailPage.tsx:5 onEdit (primary of buildOldRowMenu)',
      'pages/NamedDetailPage.tsx:8 primaryAction.onClick (cannot be followed)',
      'pages/NamedDetailPage.tsx:9 primaryAction (cannot be followed)',
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
