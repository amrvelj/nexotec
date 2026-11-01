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
// new shape fails loudly instead of passing unjudged. Inside a body, a call
// to an imported function or through an import or a global (`api.post`,
// `navigator.clipboard`) counts as real work, as does a TanStack Query
// mutation's `mutate`; inside a condition (`if (isAllocate(kind))`) such a
// call is a predicate and counts as nothing.

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
// A body is judged path by path: an `if`, a `switch` (fall-through included),
// `?:`, `&&`/`||`/`??`, a `try`/`catch`/`finally`, an early `return` and a
// `throw` each split it, and the handler fails when any one path only switches
// a tab or cannot be followed. Before, a body that was not one expression
// passed unjudged (a one-`if` dispatcher), and so did a call through a member
// (`startAction.allocate()`).
type Effect = 'none' | Verdict

/** Every effect a piece of code can have, one per way through it (at most four, so nothing explodes). */
type Ways = Effect[]

/** Running `effects` one after the other: any real work makes the whole real work. */
function inSequence(effects: Effect[]): Effect {
  for (const effect of ['ok', 'unresolved', 'tab-switch'] as const) if (effects.includes(effect)) return effect
  return 'none'
}

/** The verdict over every way: one that only switches a tab, or cannot be followed, decides. */
function worst(ways: Ways): Effect {
  for (const effect of ['tab-switch', 'unresolved', 'ok'] as const) if (ways.includes(effect)) return effect
  return 'none'
}

function distinct(ways: Ways): Ways {
  return [...new Set(ways)]
}

/** `first`, then `second`: every pairing of their ways. */
function then(first: Ways, second: Ways): Ways {
  return distinct(first.flatMap((a) => second.map((b) => inSequence([a, b]))))
}

function thenAll(list: Ways[]): Ways {
  return list.reduce(then, ['none'])
}

/** One of several alternatives runs. */
function either(...list: Ways[]): Ways {
  return distinct(list.flat())
}

/** One way through a statement list: what it did, and whether it left the function (or the switch). */
interface Path {
  effect: Effect
  done: false | 'return' | 'break'
}

type Callable = ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration | ts.MethodDeclaration

// Where code runs. In a body, a call the scan cannot read into (an import, a
// global, `navigate`) is real work. In a condition — an `if` or `switch` head,
// `?:`'s test, the left of `&&`/`||`/`??`, a `throw`'s value — it is a
// predicate and counts as nothing, so `if (isAllocate(kind))` never makes
// every branch real work. Local functions are followed in both — inside a
// condition their own calls count as predicates too, so a handler that only
// acts through a condition fails loudly rather than passing.
type Mode = 'body' | 'condition'

/** What the scan knows of one file, and each function's ways once judged (per mode). */
interface FileScope {
  decls: Map<string, ts.Node[]>
  params: Set<string>
  memo: Record<Mode, Map<ts.Node, Ways>>
  pending: Record<Mode, Set<ts.Node>>
  /** Functions met again while still being judged (recursion), innermost last. */
  cycleHits: ts.Node[]
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
  return {
    decls: declarations(file),
    params: parameterNames(file),
    memo: { body: new Map(), condition: new Map() },
    pending: { body: new Set(), condition: new Set() },
    cycleHits: [],
  }
}

function isCallTo(expr: ts.Expression, names: string[]): expr is ts.CallExpression {
  return ts.isCallExpression(expr) && ts.isIdentifier(expr.expression) && names.includes(expr.expression.text)
}

/** A call the scan does not read into: real work in a body, nothing in a condition. */
function trusted(mode: Mode): Ways {
  return [mode === 'condition' ? 'none' : 'ok']
}

function functionWays(fn: Callable, scope: FileScope, mode: Mode): Ways {
  const known = scope.memo[mode].get(fn)
  if (known) return known
  if (scope.pending[mode].has(fn)) {
    // Recursion adds nothing the first visit does not judge. A result that
    // leaned on an unfinished function is not memoised (below), so a second
    // handler entering the cycle elsewhere is judged in full.
    scope.cycleHits.push(fn)
    return ['none']
  }
  scope.pending[mode].add(fn)
  const mark = scope.cycleHits.length
  let ways: Ways = ['none']
  if (fn.body && ts.isBlock(fn.body)) ways = distinct(runStatements(fn.body.statements, scope, mode).map((p) => p.effect))
  else if (fn.body) ways = expressionWays(fn.body, scope, mode)
  scope.pending[mode].delete(fn)
  const stillOpen = scope.cycleHits.splice(mark).filter((n) => n !== fn)
  scope.cycleHits.push(...stillOpen)
  if (stillOpen.length === 0) scope.memo[mode].set(fn, ways)
  return ways
}

/** Paths that did the same and ended the same way are one path. */
function distinctPaths(paths: Path[]): Path[] {
  const seen = new Map<string, Path>()
  for (const p of paths) seen.set(`${p.effect}/${p.done}`, p)
  return [...seen.values()]
}

function runStatements(statements: readonly ts.Statement[], scope: FileScope, mode: Mode): Path[] {
  let paths: Path[] = [{ effect: 'none', done: false }]
  for (const statement of statements) {
    const next: Path[] = []
    for (const path of paths) {
      if (path.done) next.push(path)
      else next.push(...prefixed([path.effect], statementPaths(statement, scope, mode)))
    }
    paths = distinctPaths(next)
  }
  return paths
}

/** `paths`, each after one of `ways`. */
function prefixed(ways: Ways, paths: Path[]): Path[] {
  return distinctPaths(ways.flatMap((w) => paths.map((p) => ({ effect: inSequence([w, p.effect]), done: p.done }))))
}

function statementPaths(statement: ts.Statement, scope: FileScope, mode: Mode): Path[] {
  const go = (ways: Ways, done: Path['done'] = false): Path[] => ways.map((effect) => ({ effect, done }))
  if (ts.isBlock(statement)) return runStatements(statement.statements, scope, mode)
  if (ts.isExpressionStatement(statement)) return go(expressionWays(statement.expression, scope, mode))
  if (ts.isReturnStatement(statement)) {
    return go(statement.expression ? expressionWays(statement.expression, scope, mode) : ['none'], 'return')
  }
  if (ts.isThrowStatement(statement)) return go(expressionWays(statement.expression, scope, 'condition'), 'return')
  if (ts.isBreakStatement(statement) && !statement.label) return go(['none'], 'break')
  if (ts.isIfStatement(statement)) {
    const branches = [
      ...statementPaths(statement.thenStatement, scope, mode),
      ...(statement.elseStatement ? statementPaths(statement.elseStatement, scope, mode) : go(['none'])),
    ]
    return prefixed(expressionWays(statement.expression, scope, 'condition'), branches)
  }
  if (ts.isSwitchStatement(statement)) {
    const clauses = statement.caseBlock.clauses
    // Entering at each clause runs on into the next until a `break` (fall-through).
    const branches = clauses.flatMap((_, i) =>
      runStatements(
        clauses.slice(i).flatMap((c) => [...c.statements]),
        scope,
        mode,
      ).map((p) => ({ effect: p.effect, done: p.done === 'break' ? false : p.done })),
    )
    if (!clauses.some(ts.isDefaultClause)) branches.push({ effect: 'none', done: false })
    return prefixed(expressionWays(statement.expression, scope, 'condition'), branches)
  }
  if (ts.isTryStatement(statement)) {
    const tried = runStatements(statement.tryBlock.statements, scope, mode)
    // A catch runs after something in the try was attempted: it starts from what the try does.
    const caught = statement.catchClause
      ? prefixed(
          tried.map((p) => p.effect),
          runStatements(statement.catchClause.block.statements, scope, mode),
        )
      : []
    const ways = [...tried, ...caught]
    if (!statement.finallyBlock) return distinctPaths(ways)
    const cleanup = runStatements(statement.finallyBlock.statements, scope, mode)
    return distinctPaths(
      ways.flatMap((way) => cleanup.map((c) => ({ effect: inSequence([way.effect, c.effect]), done: c.done || way.done }))),
    )
  }
  if (ts.isVariableStatement(statement)) {
    const initializers = statement.declarationList.declarations.flatMap((d) => (d.initializer ? [d.initializer] : []))
    return go(thenAll(initializers.map((i) => expressionWays(i, scope, mode))))
  }
  if (
    ts.isFunctionDeclaration(statement) ||
    ts.isEmptyStatement(statement) ||
    ts.isTypeAliasDeclaration(statement) ||
    ts.isInterfaceDeclaration(statement)
  ) {
    return go(['none'])
  }
  return go(['unresolved']) // a loop, a label, …: not judged, so not passed
}

function isAssignment(kind: ts.SyntaxKind): boolean {
  return kind >= ts.SyntaxKind.FirstAssignment && kind <= ts.SyntaxKind.LastAssignment
}

function expressionWays(node: ts.Expression, scope: FileScope, mode: Mode): Ways {
  const expr = unwrap(node)
  if (ts.isVoidExpression(expr) || ts.isAwaitExpression(expr) || ts.isTypeOfExpression(expr)) {
    return expressionWays(expr.expression, scope, mode)
  }
  if (ts.isConditionalExpression(expr)) {
    return then(
      expressionWays(expr.condition, scope, 'condition'),
      either(expressionWays(expr.whenTrue, scope, mode), expressionWays(expr.whenFalse, scope, mode)),
    )
  }
  if (ts.isBinaryExpression(expr)) {
    const operator = expr.operatorToken.kind
    if (
      operator === ts.SyntaxKind.AmpersandAmpersandToken ||
      operator === ts.SyntaxKind.BarBarToken ||
      operator === ts.SyntaxKind.QuestionQuestionToken
    ) {
      return then(expressionWays(expr.left, scope, 'condition'), either(['none'], expressionWays(expr.right, scope, mode)))
    }
    const right = expressionWays(expr.right, scope, mode)
    if (isAssignment(operator)) return then(right, ['ok'])
    return then(expressionWays(expr.left, scope, mode), right)
  }
  if (ts.isCallExpression(expr)) return callWays(expr, scope, mode)
  if (ts.isNewExpression(expr) || ts.isTaggedTemplateExpression(expr)) return trusted(mode)
  if (ts.isPrefixUnaryExpression(expr) || ts.isPostfixUnaryExpression(expr)) {
    const operator = expr.operator
    return operator === ts.SyntaxKind.PlusPlusToken || operator === ts.SyntaxKind.MinusMinusToken
      ? ['ok']
      : expressionWays(expr.operand, scope, mode)
  }
  if (ts.isTemplateExpression(expr)) return thenAll(expr.templateSpans.map((s) => expressionWays(s.expression, scope, mode)))
  if (ts.isArrayLiteralExpression(expr)) return thenAll(expr.elements.map((e) => expressionWays(e, scope, mode)))
  if (ts.isSpreadElement(expr)) return expressionWays(expr.expression, scope, mode)
  if (ts.isObjectLiteralExpression(expr)) {
    return thenAll(
      expr.properties.map((p) =>
        ts.isPropertyAssignment(p)
          ? expressionWays(p.initializer, scope, mode)
          : ts.isSpreadAssignment(p)
            ? expressionWays(p.expression, scope, mode)
            : ['none'],
      ),
    )
  }
  return ['none'] // a name, a literal, a member read, a function not called
}

function callWays(call: ts.CallExpression, scope: FileScope, mode: Mode): Ways {
  if (isTabSwitchCall(call)) return ['tab-switch']
  const args = thenAll(call.arguments.map((a) => expressionWays(a, scope, mode)))
  const callee = unwrap(call.expression)
  let ways: Ways
  if (ts.isIdentifier(callee)) ways = nameCallWays(callee.text, scope, mode)
  else if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) ways = memberCallWays(callee, scope, mode)
  else if (ts.isArrowFunction(callee) || ts.isFunctionExpression(callee)) ways = functionWays(callee, scope, mode)
  else ways = ['unresolved'] // `make()()`: what is called is not in the file
  return then(args, ways)
}

/** Calling a bare name: a local function is followed; a parameter (a prop) cannot be; an import, a state setter or `navigate` is trusted. */
function nameCallWays(name: string, scope: FileScope, mode: Mode): Ways {
  const targets = scope.decls.get(name)
  if (targets) {
    return either(
      ...targets.map((t) => (ts.isFunctionDeclaration(t) ? functionWays(t, scope, mode) : localValueCallWays(t as ts.Expression, scope, mode))),
    )
  }
  return scope.params.has(name) ? ['unresolved'] : trusted(mode)
}

/** The value `useMemo(() => value)` memoises, when its factory just returns one. */
function memoisedValue(expr: ts.Expression): ts.Expression | null {
  if (!isCallTo(expr, ['useMemo']) || !expr.arguments[0]) return null
  const factory = unwrap(expr.arguments[0])
  if (!ts.isArrowFunction(factory) && !ts.isFunctionExpression(factory)) return null
  if (!ts.isBlock(factory.body)) return factory.body
  const only = factory.body.statements.length === 1 ? factory.body.statements[0] : undefined
  return only && ts.isReturnStatement(only) && only.expression ? only.expression : null
}

/** Calling a local `const` by its initializer, inside a body. A value that is no function (`useNavigate()`'s result) is trusted, as before KAN-512. */
function localValueCallWays(value: ts.Expression, scope: FileScope, mode: Mode): Ways {
  const expr = unwrap(value)
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionWays(expr, scope, mode)
  if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? localValueCallWays(expr.arguments[0], scope, mode) : ['unresolved']
  const memoised = memoisedValue(expr)
  if (memoised && !objectLiteralOf(expr)) return localValueCallWays(memoised, scope, mode)
  if (ts.isIdentifier(expr)) return expr.text === 'undefined' ? ['none'] : nameCallWays(expr.text, scope, mode)
  if (ts.isConditionalExpression(expr)) {
    return either(localValueCallWays(expr.whenTrue, scope, mode), localValueCallWays(expr.whenFalse, scope, mode))
  }
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) return memberCallWays(expr, scope, mode)
  return trusted(mode)
}

/** The object literal a local `const` holds, directly or as `useMemo(() => ({ … }))`'s value. */
function objectLiteralOf(value: ts.Node): ts.ObjectLiteralExpression | null {
  if (!ts.isExpression(value)) return null
  const expr = unwrap(value)
  if (ts.isObjectLiteralExpression(expr)) return expr
  const memoised = memoisedValue(expr)
  return memoised ? objectLiteralOf(memoised) : null
}

// Receivers whose members are server writes by definition (TanStack Query's
// `mutate`/`mutateAsync`, the client's `invalidateQueries`): trusted inside a
// body, though the scan cannot read into the library.
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

/** The members of a local object literal a member access can reach: one by name, every one for a computed key. */
function reachableMembers(access: ts.PropertyAccessExpression | ts.ElementAccessExpression, object: ts.ObjectLiteralExpression) {
  const name = memberName(access)
  return object.properties.filter((p) => name === null || propertyName(p) === name)
}

/**
 * Calling through a member inside a body — `startAction.allocate()`,
 * `handlers[kind]()`. Followed into a local object literal (a computed key
 * judges every member); a local the scan cannot read into, or a parameter,
 * cannot be followed; an import or a global (`api.post`,
 * `navigator.clipboard`) is trusted, as a call to an imported function is.
 */
function memberCallWays(access: ts.PropertyAccessExpression | ts.ElementAccessExpression, scope: FileScope, mode: Mode): Ways {
  const receiver = unwrap(access.expression)
  if (!ts.isIdentifier(receiver)) {
    const root = rootName(receiver)
    if (root === null) return ['unresolved']
    return scope.decls.has(root) || scope.params.has(root) ? ['unresolved'] : trusted(mode)
  }
  const targets = scope.decls.get(receiver.text)
  if (!targets) return scope.params.has(receiver.text) ? ['unresolved'] : trusted(mode)
  return either(
    ...targets.map((target): Ways => {
      if (ts.isExpression(target) && WRITING_HOOKS.some((hook) => isCallTo(unwrap(target), [hook]))) return trusted(mode)
      const object = objectLiteralOf(target)
      if (!object) return ['unresolved']
      const members = reachableMembers(access, object)
      if (members.length === 0) return ['unresolved']
      return either(
        ...members.map((member): Ways => {
          if (ts.isPropertyAssignment(member)) return localValueCallWays(member.initializer, scope, mode)
          if (ts.isShorthandPropertyAssignment(member)) return nameCallWays(member.name.text, scope, mode)
          if (ts.isMethodDeclaration(member)) return functionWays(member, scope, mode)
          return ['unresolved']
        }),
      )
    }),
  )
}

/**
 * The ways of a handler value — what a click calls. KAN-468's rule holds for
 * the value itself: it must resolve, in the file, to a function the scan can
 * read. A name or member that is a prop, an import or a local holding
 * anything else (`useStartAction()`'s result, `importedHandler`) cannot be
 * followed. Only inside the function it resolves to are imports trusted.
 */
function handlerWays(value: ts.Expression, scope: FileScope): Ways {
  const expr = unwrap(value)
  if (ts.isIdentifier(expr)) {
    if (expr.text === 'undefined') return ['none']
    const targets = scope.decls.get(expr.text)
    if (!targets) return ['unresolved']
    return either(...targets.map((t) => (ts.isFunctionDeclaration(t) ? functionWays(t, scope, 'body') : handlerWays(t as ts.Expression, scope))))
  }
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionWays(expr, scope, 'body')
  if (ts.isConditionalExpression(expr)) return either(handlerWays(expr.whenTrue, scope), handlerWays(expr.whenFalse, scope))
  if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? handlerWays(expr.arguments[0], scope) : ['unresolved']
  const memoised = memoisedValue(expr)
  if (memoised) return handlerWays(memoised, scope)
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) {
    const receiver = unwrap(expr.expression)
    const targets = ts.isIdentifier(receiver) ? scope.decls.get(receiver.text) : undefined
    if (!targets) return ['unresolved']
    return either(
      ...targets.map((target): Ways => {
        const object = objectLiteralOf(target)
        if (!object) return ['unresolved']
        const members = reachableMembers(expr, object)
        if (members.length === 0) return ['unresolved']
        return either(
          ...members.map((member): Ways => {
            if (ts.isPropertyAssignment(member)) return handlerWays(member.initializer, scope)
            if (ts.isShorthandPropertyAssignment(member)) return handlerWays(member.name, scope)
            if (ts.isMethodDeclaration(member)) return functionWays(member, scope, 'body')
            return ['unresolved']
          }),
        )
      }),
    )
  }
  return ['unresolved']
}

/** Judges a handler value: `ok`, a pure tab switch, or something the scan cannot follow. */
function judge(value: ts.Expression, scope: FileScope): Verdict {
  const effect = worst(handlerWays(value, scope))
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

  // KAN-512 review: each rule of the path judge pinned by a case that turns
  // green if the rule is removed, and the KAN-468 handler-value rule kept.
  it('pins every path rule and keeps a handler value that cannot be read refused', () => {
    const builders = primaryHandlersOfBuilders([
      {
        path: 'components/oldRowMenu.tsx',
        text: `export function buildOldRowMenu(t, handlers) {
          return { primary: { label: t('allocate'), onClick: handlers.onAllocate }, overflow: {} }
        }`,
      },
    ])
    /** The verdict for one page whose primary is `handler`, given `setup` before it. */
    const verdict = (setup: string, handler: string): string => {
      const text = `export function A() {
        ${setup}
        const rowMenu = buildOldRowMenu(t, { onAllocate: ${handler} })
        return <DetailHeader primaryAction={rowMenu.primary} />
      }`
      const found = noOpPrimaries([{ path: 'pages/P.tsx', text }], builders)
      if (found.length === 0) return 'ok'
      return found[0].endsWith('(cannot be followed)') ? 'cannot be followed' : 'tab switch'
    }

    // A handler value must resolve to a function the file shows (KAN-468); none of these may pass.
    expect(verdict(`const startAction = useStartAction()`, 'startAction')).toBe('cannot be followed')
    expect(verdict(`const h = importedHandler`, 'h')).toBe('cannot be followed')
    expect(verdict(`const h = useCallback(importedHandler, [])`, 'h')).toBe('cannot be followed')
    expect(verdict('', 'ready ? importedHandler : undefined')).toBe('cannot be followed')
    expect(verdict(`const h = useMemo(() => () => setActiveTab('identity'), [])`, 'h')).toBe('tab switch')
    expect(verdict(`const create = useMutation({ mutationFn })`, 'create.mutate')).toBe('cannot be followed')

    // A call in a condition is a predicate: it never makes every branch real work.
    expect(verdict('', "() => { if (!canAllocate(v)) return; setActiveTab('identity') }")).toBe('tab switch')
    expect(verdict('', "() => isEditable(v) && setActiveTab('identity')")).toBe('tab switch')
    expect(verdict('', "() => (isEditable(v) ? setActiveTab('identity') : setAllocating(true))")).toBe('tab switch')
    expect(
      verdict(
        "function startAction(kind) { if (isAllocate(kind)) { setActiveTab('identity') } else { setFormToFocus(kind) } }",
        "() => startAction('allocate')",
      ),
    ).toBe('tab switch')
    expect(verdict('', "() => { switch (String(kind)) { case 'allocate': setActiveTab('identity') } }")).toBe('tab switch')
    // … while a local predicate is still followed (a tab switch inside it counts), and a call in a body stays real work.
    expect(verdict("const isLocked = () => { setActiveTab('identity'); return locked }", '() => { if (isLocked()) return }')).toBe('tab switch')
    expect(verdict('', "() => { if (busy) return; void api.post('/x') }")).toBe('ok')

    // `||` and `??` run their right side on one path only.
    expect(verdict('', "() => ready || setActiveTab('identity')")).toBe('tab switch')
    expect(verdict('', "() => value ?? setActiveTab('identity')")).toBe('tab switch')
    // `throw` leaves the function: the work after it is not on that path.
    expect(verdict('', "() => { if (editing) { setActiveTab('identity'); throw 'stop' } setAllocating(true) }")).toBe('tab switch')
    // A catch after a try that did nothing; a finally that runs on every path.
    expect(verdict('', "() => { try { if (!valid) throw 'invalid' } catch { setActiveTab('identity') } }")).toBe('tab switch')
    expect(verdict('', "() => { try { if (busy) return } finally { setActiveTab('identity') } }")).toBe('tab switch')
    // A `break` ends the switch, not the handler; fall-through runs on into the next clause.
    expect(verdict('', "() => { switch (kind) { case 'a': setActiveTab('identity'); break } setAllocating(true) }")).toBe('ok')
    expect(
      verdict('', "() => { switch (kind) { case 'a': setActiveTab('identity') /* falls through */ case 'b': setAllocating(true); break } }"),
    ).toBe('ok')
    // A loop is not judged, so it is not passed.
    expect(verdict('', '() => { for (const k of kinds) setFormToFocus(k) }')).toBe('cannot be followed')
    // A computed key on a local object judges every member.
    expect(
      verdict(
        "const startAllocating = () => setActiveTab('identity'); const actions = { allocate: startAllocating, odometer: () => setFormToFocus('odometer') }",
        '() => actions[kind]()',
      ),
    ).toBe('tab switch')
    // The same function twice in one body is a tab switch twice, not real work the second time.
    expect(verdict("const go = () => setActiveTab('identity')", '() => { go(); go() }')).toBe('tab switch')
    // A recursive call still being judged adds nothing: it does not turn the tab switch into work
    // (an unending recursion, which only a test writes, is the one shape where that shows).
    expect(verdict("function f() { setActiveTab('identity'); f() }", '() => f()')).toBe('tab switch')
    // An expression's alternatives are paths too: on the `ready` path only the tab switch runs.
    expect(verdict('', "() => { ready || setAllocating(true); setActiveTab('identity') }")).toBe('tab switch')
    expect(verdict('', "() => { (ready ? setAllocating(true) : null); setActiveTab('identity') }")).toBe('tab switch')
    // Many branches stay judgeable: paths that did the same are merged, never cut off.
    const ifs = Array.from({ length: 12 }, (_, i) => `if (c${i}) setFormToFocus('a${i}'); else setFormToFocus('b${i}');`).join(' ')
    expect(verdict('', `() => { ${ifs} }`)).toBe('ok')
    expect(verdict('', `() => { if (busy) { setActiveTab('identity'); return } ${ifs} }`)).toBe('tab switch')

    // Mutual recursion: whichever primary is judged first, both see the tab switch.
    const cycle = `export function A() {
      const g = () => f()
      const f = () => { if (k) { g() } else { setActiveTab('identity') } }
      const first = buildOldRowMenu(t, { onAllocate: () => f() })
      const second = buildOldRowMenu(t, { onAllocate: () => g() })
      return <DetailHeader primaryAction={first.primary} />
    }`
    expect(noOpPrimaries([{ path: 'pages/P.tsx', text: cycle }], builders)).toEqual([
      'pages/P.tsx:4 onAllocate (primary of buildOldRowMenu)',
      'pages/P.tsx:5 onAllocate (primary of buildOldRowMenu)',
    ])
  })
})
