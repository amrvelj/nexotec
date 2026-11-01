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
// (`const goOverview = () => setActiveTab('overview')`, `onEdit: goOverview`),
// a function it calls and a member of a local object it calls through
// (`startAction.allocate()`, `handlers['allocate']()`) are followed to their
// declaration in the file. A body is judged path by path (KAN-512): every
// branch of an `if`, a `switch`, `?:` and `&&`/`||`/`??`, an early `return`
// and a `catch`. Refused: a handler with one path whose only effect is a
// `set…Tab(…)` call or a `setSearchParams(…)` naming a tab — and a primary
// the scan cannot follow (a prop, an import or a member used as the handler
// itself, a call through a prop or a local it cannot read into, a loop), so a
// new shape fails loudly instead of passing unjudged. A call to an imported
// function or through an import or a global (`api.post`, `navigator.clipboard`)
// counts as real work, as does a TanStack Query mutation's `mutate`.

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

function isTabSwitchCall(expr: ts.Expression): boolean {
  if (ts.isVoidExpression(expr)) expr = unwrap(expr.expression)
  if (!ts.isCallExpression(expr) || !ts.isIdentifier(expr.expression)) return false
  const callee = expr.expression.text
  if (/^set\w*Tab$/.test(callee)) return true
  return callee === 'setSearchParams' && expr.arguments.some((a) => /\btab\b/.test(a.getText()))
}

type Verdict = 'ok' | 'tab-switch' | 'unresolved'

// KAN-512: what running a piece of code does, as far as the scan can tell —
// nothing, real work (`ok`), only a tab switch, or something it cannot follow.
// A body is judged path by path: an `if`, a `switch`, `?:`, `&&`/`||`/`??`,
// a `try`/`catch` and an early `return` each split it, and the handler fails
// when any one path only switches a tab or cannot be followed. Before, a
// body that was not one expression passed unjudged (a one-`if` dispatcher),
// and so did a call through a member (`startAction.allocate()`).
type Effect = 'none' | Verdict

/** Running `effects` one after the other: any real work makes the whole real work. */
function inSequence(effects: Effect[]): Effect {
  for (const effect of ['ok', 'unresolved', 'tab-switch'] as const) if (effects.includes(effect)) return effect
  return 'none'
}

/** Exactly one of `effects` runs: one path that only switches a tab, or cannot be followed, decides. */
function oneOf(effects: Effect[]): Effect {
  for (const effect of ['tab-switch', 'unresolved', 'ok'] as const) if (effects.includes(effect)) return effect
  return 'none'
}

/** One way through a statement list: what it did, and whether it left the function (or the switch clause). */
interface Path {
  effect: Effect
  done: false | 'return' | 'break'
}

const MAX_PATHS = 64

type Callable = ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration | ts.MethodDeclaration

/** What the scan knows of one file: its declarations, the names bound as parameters, and each function's effect once judged. */
interface FileScope {
  decls: Map<string, ts.Node[]>
  params: Set<string>
  memo: Map<ts.Node, Effect | 'pending'>
}

/** Every name a function parameter binds, destructured ones included (`({ onEdit }) => …`). */
function parameterNames(file: ts.SourceFile): Set<string> {
  const names = new Set<string>()
  const bind = (name: ts.BindingName) => {
    if (ts.isIdentifier(name)) names.add(name.text)
    else for (const element of name.elements) if (ts.isBindingElement(element)) bind(element.name)
  }
  walk(file, (n) => {
    if (ts.isParameter(n)) bind(n.name)
  })
  return names
}

function fileScope(file: ts.SourceFile): FileScope {
  return { decls: declarations(file), params: parameterNames(file), memo: new Map() }
}

function isCallTo(expr: ts.Expression, names: string[]): expr is ts.CallExpression {
  return ts.isCallExpression(expr) && ts.isIdentifier(expr.expression) && names.includes(expr.expression.text)
}

function functionEffect(fn: Callable, scope: FileScope): Effect {
  const known = scope.memo.get(fn)
  if (known === 'pending') return 'none' // recursion adds nothing the first visit does not judge
  if (known) return known
  scope.memo.set(fn, 'pending')
  let effect: Effect = 'none'
  if (fn.body && ts.isBlock(fn.body)) effect = oneOf(runStatements(fn.body.statements, scope).map((p) => p.effect))
  else if (fn.body) effect = expressionEffect(fn.body, scope)
  scope.memo.set(fn, effect)
  return effect
}

function runStatements(statements: readonly ts.Statement[], scope: FileScope, start: Path[] = [{ effect: 'none', done: false }]): Path[] {
  let paths = start
  for (const statement of statements) {
    const next: Path[] = []
    for (const path of paths) {
      if (path.done) {
        next.push(path)
        continue
      }
      for (const step of statementPaths(statement, scope)) {
        next.push({ effect: inSequence([path.effect, step.effect]), done: step.done })
      }
    }
    if (next.length > MAX_PATHS) return [{ effect: 'unresolved', done: 'return' }]
    paths = next
  }
  return paths
}

function prefixed(effect: Effect, paths: Path[]): Path[] {
  return paths.map((p) => ({ effect: inSequence([effect, p.effect]), done: p.done }))
}

function statementPaths(statement: ts.Statement, scope: FileScope): Path[] {
  const go = (effect: Effect): Path[] => [{ effect, done: false }]
  if (ts.isBlock(statement)) return runStatements(statement.statements, scope)
  if (ts.isExpressionStatement(statement)) return go(expressionEffect(statement.expression, scope))
  if (ts.isReturnStatement(statement)) {
    return [{ effect: statement.expression ? expressionEffect(statement.expression, scope) : 'none', done: 'return' }]
  }
  if (ts.isThrowStatement(statement)) return [{ effect: expressionEffect(statement.expression, scope), done: 'return' }]
  if (ts.isBreakStatement(statement) && !statement.label) return [{ effect: 'none', done: 'break' }]
  if (ts.isIfStatement(statement)) {
    const branches = [
      ...statementPaths(statement.thenStatement, scope),
      ...(statement.elseStatement ? statementPaths(statement.elseStatement, scope) : go('none')),
    ]
    return prefixed(expressionEffect(statement.expression, scope), branches)
  }
  if (ts.isSwitchStatement(statement)) {
    const clauses = statement.caseBlock.clauses
    const branches = clauses.flatMap((clause) =>
      runStatements(clause.statements, scope).map((p) => ({ effect: p.effect, done: p.done === 'break' ? false : p.done })),
    )
    if (!clauses.some(ts.isDefaultClause)) branches.push({ effect: 'none', done: false })
    return prefixed(expressionEffect(statement.expression, scope), branches)
  }
  if (ts.isTryStatement(statement)) {
    const tried = runStatements(statement.tryBlock.statements, scope)
    // A catch runs after something in the try was attempted: it starts from what the try does.
    const caught = statement.catchClause
      ? prefixed(oneOf(tried.map((p) => p.effect)), runStatements(statement.catchClause.block.statements, scope))
      : []
    const ways = [...tried, ...caught]
    if (!statement.finallyBlock) return ways
    const cleanup = runStatements(statement.finallyBlock.statements, scope)
    return ways.flatMap((way) => cleanup.map((c) => ({ effect: inSequence([way.effect, c.effect]), done: c.done || way.done })))
  }
  if (ts.isVariableStatement(statement)) {
    const initializers = statement.declarationList.declarations.flatMap((d) => (d.initializer ? [d.initializer] : []))
    return go(inSequence(initializers.map((i) => expressionEffect(i, scope))))
  }
  if (
    ts.isFunctionDeclaration(statement) ||
    ts.isEmptyStatement(statement) ||
    ts.isTypeAliasDeclaration(statement) ||
    ts.isInterfaceDeclaration(statement)
  ) {
    return go('none')
  }
  return go('unresolved') // a loop, a label, …: not judged, so not passed
}

function isAssignment(kind: ts.SyntaxKind): boolean {
  return kind >= ts.SyntaxKind.FirstAssignment && kind <= ts.SyntaxKind.LastAssignment
}

function expressionEffect(node: ts.Expression, scope: FileScope): Effect {
  const expr = unwrap(node)
  if (ts.isVoidExpression(expr) || ts.isAwaitExpression(expr) || ts.isTypeOfExpression(expr)) {
    return expressionEffect(expr.expression, scope)
  }
  if (ts.isConditionalExpression(expr)) {
    return inSequence([
      expressionEffect(expr.condition, scope),
      oneOf([expressionEffect(expr.whenTrue, scope), expressionEffect(expr.whenFalse, scope)]),
    ])
  }
  if (ts.isBinaryExpression(expr)) {
    const left = expressionEffect(expr.left, scope)
    const right = expressionEffect(expr.right, scope)
    const operator = expr.operatorToken.kind
    if (
      operator === ts.SyntaxKind.AmpersandAmpersandToken ||
      operator === ts.SyntaxKind.BarBarToken ||
      operator === ts.SyntaxKind.QuestionQuestionToken
    ) {
      return inSequence([left, oneOf(['none', right])])
    }
    if (isAssignment(operator)) return inSequence([right, 'ok'])
    return inSequence([left, right])
  }
  if (ts.isCallExpression(expr)) return callEffect(expr, scope)
  if (ts.isNewExpression(expr) || ts.isTaggedTemplateExpression(expr)) return 'ok'
  if (ts.isPrefixUnaryExpression(expr) || ts.isPostfixUnaryExpression(expr)) {
    const operator = expr.operator
    return operator === ts.SyntaxKind.PlusPlusToken || operator === ts.SyntaxKind.MinusMinusToken
      ? 'ok'
      : expressionEffect(expr.operand, scope)
  }
  if (ts.isTemplateExpression(expr)) return inSequence(expr.templateSpans.map((s) => expressionEffect(s.expression, scope)))
  if (ts.isArrayLiteralExpression(expr)) return inSequence(expr.elements.map((e) => expressionEffect(e, scope)))
  if (ts.isSpreadElement(expr)) return expressionEffect(expr.expression, scope)
  if (ts.isObjectLiteralExpression(expr)) {
    return inSequence(
      expr.properties.map((p) =>
        ts.isPropertyAssignment(p) ? expressionEffect(p.initializer, scope) : ts.isSpreadAssignment(p) ? expressionEffect(p.expression, scope) : 'none',
      ),
    )
  }
  return 'none' // a name, a literal, a member read, a function not called
}

function callEffect(call: ts.CallExpression, scope: FileScope): Effect {
  if (isTabSwitchCall(call)) return 'tab-switch'
  const args = inSequence(call.arguments.map((a) => expressionEffect(a, scope)))
  const callee = unwrap(call.expression)
  let effect: Effect
  if (ts.isIdentifier(callee)) effect = nameCallEffect(callee.text, scope)
  else if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) effect = memberCallEffect(callee, scope)
  else if (ts.isArrowFunction(callee) || ts.isFunctionExpression(callee)) effect = functionEffect(callee, scope)
  else effect = 'unresolved' // `make()()`: what is called is not in the file
  return inSequence([args, effect])
}

/** Calling a bare name: a local function is followed; a parameter (a prop) cannot be; anything else — an import, a state setter, `navigate` — is real work. */
function nameCallEffect(name: string, scope: FileScope): Effect {
  const targets = scope.decls.get(name)
  if (targets) {
    return oneOf(targets.map((t) => (ts.isFunctionDeclaration(t) ? functionEffect(t, scope) : localValueCallEffect(t as ts.Expression, scope))))
  }
  return scope.params.has(name) ? 'unresolved' : 'ok'
}

/** Calling a local `const` by its initializer. A value that is no function (`useNavigate()`'s result) is real work, as before KAN-512. */
function localValueCallEffect(value: ts.Expression, scope: FileScope): Effect {
  const expr = unwrap(value)
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionEffect(expr, scope)
  if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? valueCallEffect(expr.arguments[0], scope) : 'unresolved'
  if (ts.isIdentifier(expr)) return nameCallEffect(expr.text, scope)
  if (ts.isConditionalExpression(expr)) return oneOf([valueCallEffect(expr.whenTrue, scope), valueCallEffect(expr.whenFalse, scope)])
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) return memberCallEffect(expr, scope)
  return 'ok'
}

/** Calling a value written in place: a function, a name, a member — what a handler is. */
function valueCallEffect(value: ts.Expression, scope: FileScope): Effect {
  const expr = unwrap(value)
  if (ts.isIdentifier(expr)) return expr.text === 'undefined' ? 'none' : nameCallEffect(expr.text, scope)
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionEffect(expr, scope)
  if (ts.isConditionalExpression(expr)) return oneOf([valueCallEffect(expr.whenTrue, scope), valueCallEffect(expr.whenFalse, scope)])
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) return memberCallEffect(expr, scope)
  if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? valueCallEffect(expr.arguments[0], scope) : 'unresolved'
  return 'unresolved'
}

/** The object literal a local `const` holds, directly or as `useMemo(() => ({ … }))`'s value. */
function objectLiteralOf(value: ts.Node): ts.ObjectLiteralExpression | null {
  if (!ts.isExpression(value)) return null
  const expr = unwrap(value)
  if (ts.isObjectLiteralExpression(expr)) return expr
  if (isCallTo(expr, ['useMemo']) && expr.arguments[0]) {
    const factory = unwrap(expr.arguments[0])
    if (ts.isArrowFunction(factory) || ts.isFunctionExpression(factory)) {
      if (!ts.isBlock(factory.body)) return objectLiteralOf(factory.body)
      const only = factory.body.statements.length === 1 ? factory.body.statements[0] : undefined
      if (only && ts.isReturnStatement(only) && only.expression) return objectLiteralOf(only.expression)
    }
  }
  return null
}

// A receiver whose members are server writes by definition (TanStack Query's
// `mutate`/`mutateAsync`, the client's `invalidateQueries`): real work, though
// the scan cannot read into the library.
const WRITING_HOOKS = ['useMutation', 'useQueryClient']

function memberName(access: ts.PropertyAccessExpression | ts.ElementAccessExpression): string | null {
  if (ts.isPropertyAccessExpression(access)) return access.name.text
  const key = unwrap(access.argumentExpression)
  return ts.isStringLiteral(key) || ts.isNoSubstitutionTemplateLiteral(key) ? key.text : null
}

function rootName(expr: ts.Expression): string | null {
  const e = unwrap(expr)
  if (ts.isIdentifier(e)) return e.text
  if (ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e)) return rootName(e.expression)
  return null
}

/**
 * Calling through a member — `startAction.allocate()`, `handlers[kind]()`.
 * Followed into a local object literal (a dynamic key judges every member);
 * a local the scan cannot read into, or a parameter, cannot be followed; an
 * import or a global (`api.post`, `navigator.clipboard`) is real work, as a
 * call to an imported function is.
 */
function memberCallEffect(access: ts.PropertyAccessExpression | ts.ElementAccessExpression, scope: FileScope): Effect {
  const receiver = unwrap(access.expression)
  if (!ts.isIdentifier(receiver)) {
    const root = rootName(receiver)
    if (root === null) return 'unresolved'
    return scope.decls.has(root) || scope.params.has(root) ? 'unresolved' : 'ok'
  }
  const targets = scope.decls.get(receiver.text)
  if (!targets) return scope.params.has(receiver.text) ? 'unresolved' : 'ok'
  const name = memberName(access)
  return oneOf(
    targets.map((target) => {
      if (ts.isExpression(target) && WRITING_HOOKS.some((hook) => isCallTo(unwrap(target), [hook]))) return 'ok'
      const object = objectLiteralOf(target)
      if (!object) return 'unresolved'
      const members = object.properties.filter((p) => name === null || propertyName(p) === name)
      if (members.length === 0) return 'unresolved'
      return oneOf(
        members.map((member) => {
          if (ts.isPropertyAssignment(member)) return valueCallEffect(member.initializer, scope)
          if (ts.isShorthandPropertyAssignment(member)) return nameCallEffect(member.name.text, scope)
          if (ts.isMethodDeclaration(member)) return functionEffect(member, scope)
          return 'unresolved'
        }),
      )
    }),
  )
}

/**
 * Judges a handler value — the thing a click calls: `ok`, a pure tab switch,
 * or something the scan cannot follow. A bare name or member must resolve in
 * the file: `onClick: onEditProp` or `onClick: actions.allocate` on a prop or
 * an import cannot be followed.
 */
function judge(value: ts.Expression, scope: FileScope): Verdict {
  const expr = unwrap(value)
  if (ts.isIdentifier(expr) && expr.text !== 'undefined' && !scope.decls.has(expr.text)) return 'unresolved'
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) {
    const receiver = unwrap(expr.expression)
    if (!ts.isIdentifier(receiver) || !scope.decls.has(receiver.text)) return 'unresolved'
  }
  const effect = valueCallEffect(expr, scope)
  return effect === 'none' ? 'ok' : effect
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
    const scope = fileScope(file)
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
            report(prop, `${name} (primary of ${node.expression.text})`, value ? judge(value, scope) : 'unresolved')
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
          report(n, 'primaryAction.onClick', judge(n.initializer, scope))
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

  // KAN-512: the two shapes the KAN-503 review found passing unjudged, and
  // their relatives — each body cut down to a tab switch on one path.
  it('judges every branch of a dispatcher and follows a call through a member', () => {
    const builders = primaryHandlersOfBuilders([
      {
        path: 'components/oldRowMenu.tsx',
        text: `export function buildOldRowMenu(t, handlers) {
          return { primary: { label: t('allocate'), onClick: handlers.onAllocate }, overflow: {} }
        }`,
      },
    ])
    const page = (body: string): Source => ({ path: 'pages/DispatchDetailPage.tsx', text: body })

    // One `if` (and a `switch`) around the allocate branch: KAN-503's `startAction`.
    const oneIf = page(`export function A() {
      function startAction(kind) {
        if (kind === 'allocate') { setActiveTab('identity') } else { setActiveTab('odometer'); setFormToFocus('odometer') }
      }
      const rowMenu = buildOldRowMenu(t, { onAllocate: () => startAction('allocate') })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    const oneSwitch = page(`export function A() {
      const startAction = (kind) => {
        switch (kind) {
          case 'allocate': setActiveTab('identity'); break
          default: setFormToFocus(kind)
        }
      }
      const rowMenu = buildOldRowMenu(t, { onAllocate: () => startAction('allocate') })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    // A forwarding arrow to a member: the review's mutation run.
    const member = page(`export function A() {
      const startAllocating = () => setActiveTab('identity')
      const startAction = { allocate: startAllocating, odometer: () => setFormToFocus('odometer') }
      const rowMenu = buildOldRowMenu(t, { onAllocate: () => startAction.allocate() })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    const memoised = page(`export function A() {
      const startAction = useMemo(() => ({ allocate() { setActiveTab('identity') } }), [])
      const rowMenu = buildOldRowMenu(t, { onAllocate: () => startAction['allocate']() })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    // Paths: a guard, an early return, `&&`, `?:` — each leaves one path that only selects a tab.
    const paths = page(`export function A() {
      const guarded = () => { if (busy) return; setActiveTab('identity') }
      const early = () => { if (editing) { setActiveTab('identity'); return } setAllocating(true) }
      const rowMenu = buildOldRowMenu(t, { onAllocate: guarded })
      const second = buildOldRowMenu(t, { onAllocate: early })
      const third = buildOldRowMenu(t, { onAllocate: () => canAllocate && setActiveTab('identity') })
      const fourth = buildOldRowMenu(t, { onAllocate: () => (canAllocate ? setAllocating(true) : setActiveTab('identity')) })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    expect(noOpPrimaries([oneIf, oneSwitch, member, memoised], builders)).toEqual([
      'pages/DispatchDetailPage.tsx:5 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:8 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:4 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:3 onAllocate (primary of buildOldRowMenu)',
    ])
    expect(noOpPrimaries([paths], builders)).toEqual([
      'pages/DispatchDetailPage.tsx:4 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:5 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:6 onAllocate (primary of buildOldRowMenu)',
      'pages/DispatchDetailPage.tsx:7 onAllocate (primary of buildOldRowMenu)',
    ])

    // What the scan cannot read into fails loudly: a hook's object, a prop, a computed key.
    const opaque = page(`export function A({ onAllocateProp, actions }) {
      const startAction = useStartActions()
      const rowMenu = buildOldRowMenu(t, { onAllocate: () => startAction.allocate() })
      const second = buildOldRowMenu(t, { onAllocate: () => onAllocateProp() })
      const third = buildOldRowMenu(t, { onAllocate: () => actions[kind]() })
      return <DetailHeader primaryAction={rowMenu.primary} />
    }`)
    expect(noOpPrimaries([opaque], builders)).toEqual([
      'pages/DispatchDetailPage.tsx:3 onAllocate (primary of buildOldRowMenu) (cannot be followed)',
      'pages/DispatchDetailPage.tsx:4 onAllocate (primary of buildOldRowMenu) (cannot be followed)',
      'pages/DispatchDetailPage.tsx:5 onAllocate (primary of buildOldRowMenu) (cannot be followed)',
    ])

    // The same shapes doing real work on every path pass.
    const working = page(`export function A() {
      function startAction(kind) {
        if (kind === 'allocate') { setActiveTab('identity'); setAllocating(true) } else { setFormToFocus(kind) }
      }
      const startAllocating = () => { setActiveTab('identity'); setAllocating(true) }
      const actions = { allocate: startAllocating }
      const create = useMutation({ mutationFn: allocate })
      const save = async () => {
        if (!vehicle) return
        try { await api.post('/x'); navigate('/y') } catch (err) { setActiveTab('identity'); setError(err) } finally { setBusy(false) }
      }
      const a = buildOldRowMenu(t, { onAllocate: () => startAction('allocate') })
      const b = buildOldRowMenu(t, { onAllocate: () => actions.allocate() })
      const c = buildOldRowMenu(t, { onAllocate: () => actions['allocate']() })
      const d = buildOldRowMenu(t, { onAllocate: () => create.mutate() })
      const e = buildOldRowMenu(t, { onAllocate: () => void save() })
      const f = buildOldRowMenu(t, { onAllocate: () => void navigator.clipboard.writeText(vin) })
      return <DetailHeader primaryAction={a.primary} />
    }`)
    expect(noOpPrimaries([working], builders)).toEqual([])
  })
})
