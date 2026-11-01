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
// detail screen one primary; this guard refuses a primary that can end up
// doing nothing but selecting a tab (a tab switch is a no-op on that tab).
//
// What it reads, with the TypeScript compiler:
// - every `build…RowMenu` builder: the handler names its `primary` can come
//   from (`primary: newOffer ?? linkVehicle`, each an object whose `onClick`
//   is `<handlers parameter>.onX`);
// - every `use…RowMenu` hook: the object it returns (KAN-512), so a call
//   through it (`menu.requestDeactivation(location)`) is followed into it;
// - every file that renders `<DetailHeader`: the value passed for each of
//   those handlers to the builder, and the `onClick` of an inline
//   `primaryAction={{ … }}`.
// A value is judged through the names it uses: a handler bound to a name
// (`const goOverview = () => setActiveTab('overview')`, `onEdit: goOverview`),
// a function it calls and a member of a local object or a row-menu hook it
// calls through (`startAction.allocate()`, `handlers['allocate']()`) are
// followed to their declaration. A body is judged path by path (KAN-512):
// every branch of an `if`, a `switch`, `?:` and `&&`/`||`/`??`, an early
// `return`, a `throw` (also out of a called function), a `catch` and a
// `finally`. Refused: a handler with one path whose only effect is a
// `set…Tab(…)` call or a `setSearchParams(…)` naming a tab — and a primary
// the scan cannot follow (a handler value that is a prop, an import, or a
// local or member holding anything but a function it can read; a call through
// a prop, through a local it cannot read into, or through a nested or missing
// member or a spread; a call to a prop; a loop; a name cycle), so a new shape
// fails loudly instead of passing unjudged. Inside a body, a call to an
// imported function or through an import or a global (`api.post`,
// `navigator.clipboard`) counts as real work, as does a TanStack Query
// mutation's `mutate`; inside a condition (`if (isAllocate(kind))`) such a
// call is a predicate and counts as nothing. A row-menu hook is followed only
// through the page's import, when one file declares it. Known misses
// (KAN-518): a call to a destructured local, a function a hook returns and a
// callback handed to an import are trusted; a `let` handler is read by its
// first value.

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
// `throw` (also one out of a called function) each split it, and the handler
// fails when any one path only switches a tab or cannot be followed. Before, a
// body that was not one expression passed unjudged (a one-`if` dispatcher),
// and so did a call through a member (`startAction.allocate()`).
type Effect = 'none' | Verdict

/** Running `effects` one after the other: any real work makes the whole real work. */
function inSequence(effects: Effect[]): Effect {
  for (const effect of ['ok', 'unresolved', 'tab-switch'] as const) if (effects.includes(effect)) return effect
  return 'none'
}

/** The verdict over every way: one that only switches a tab, or cannot be followed, decides. */
function worst(effects: Effect[]): Effect {
  for (const effect of ['tab-switch', 'unresolved', 'ok'] as const) if (effects.includes(effect)) return effect
  return 'none'
}

/** One way a piece of code can go: what it did, and whether it ended by throwing. */
interface Way {
  effect: Effect
  threw: boolean
}

/** Every way a piece of code can go — at most eight distinct ones, so the count never explodes. */
type Ways = Way[]

const way = (effect: Effect, threw = false): Way => ({ effect, threw })
const NONE: Ways = [way('none')]
const WORK: Ways = [way('ok')]
const UNRESOLVED: Ways = [way('unresolved')]

function distinct(ways: Ways): Ways {
  const seen = new Map<string, Way>()
  for (const w of ways) seen.set(`${w.effect}/${w.threw}`, w)
  return [...seen.values()]
}

/** `first`, then `second` — unless `first` threw: every pairing of their ways. */
function then(first: Ways, second: Ways): Ways {
  return distinct(first.flatMap((a) => (a.threw ? [a] : second.map((b) => way(inSequence([a.effect, b.effect]), b.threw)))))
}

function thenAll(list: Ways[]): Ways {
  return list.reduce(then, NONE)
}

/** One of several alternatives runs. */
function either(...list: Ways[]): Ways {
  return distinct(list.flat())
}

/** One way through a statement list: what it did, and whether (and how) it left the function or the switch. */
interface Path {
  effect: Effect
  done: false | 'return' | 'break' | 'throw'
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

/** A `use…RowMenu` hook (ADR-061's one definition of an entity's actions) and the object it returns, read across files like a `build…RowMenu` builder. */
interface RowMenuHook {
  scope: FileScope
  returned: ts.ObjectLiteralExpression
}

/** Hook name → the hook, or `ambiguous` when two files declare that name (which one a page means is then not known). */
type RowMenuHooks = Map<string, RowMenuHook | 'ambiguous'>

/**
 * Judging state shared by every file of one run, keyed by syntax node: a
 * cycle through two files (two row-menu hooks calling each other) is then
 * seen as one, never memoised half-judged in one file's scope.
 */
interface JudgeState {
  memo: Record<Mode, Map<ts.Node, Ways>>
  pending: Record<Mode, Set<ts.Node>>
  /** Functions met again while still being judged (recursion), innermost last. */
  cycleHits: ts.Node[]
  /** Values being resolved right now: `const p = q; const q = p` cannot be followed, never a stack overflow. */
  resolving: Set<ts.Node>
}

function judgeState(): JudgeState {
  return { memo: { body: new Map(), condition: new Map() }, pending: { body: new Set(), condition: new Set() }, cycleHits: [], resolving: new Set() }
}

/** What the scan knows of one file. */
interface FileScope extends JudgeState {
  decls: Map<string, ts.Node[]>
  params: Set<string>
  /** Names the file imports: a row-menu hook is followed only through its import. */
  imports: Set<string>
  rowMenuHooks: RowMenuHooks
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

/** The names a file's `import` declarations bind. */
function importedNames(file: ts.SourceFile): Set<string> {
  const names = new Set<string>()
  for (const statement of file.statements) {
    const clause = ts.isImportDeclaration(statement) ? statement.importClause : undefined
    if (!clause) continue
    if (clause.name) names.add(clause.name.text)
    const bindings = clause.namedBindings
    if (bindings && ts.isNamedImports(bindings)) for (const element of bindings.elements) names.add(element.name.text)
  }
  return names
}

function fileScope(file: ts.SourceFile, rowMenuHooks: RowMenuHooks = new Map(), state: JudgeState = judgeState()): FileScope {
  return { ...state, decls: declarations(file), params: parameterNames(file), imports: importedNames(file), rowMenuHooks }
}

/**
 * Every `function use…RowMenu()` in `sources` whose body is a single
 * definition ending in `return { … }` (an early `return` makes it no row-menu
 * hook the scan reads). A name two files declare is ambiguous.
 */
function rowMenuHooksOf(sources: Source[], state: JudgeState): RowMenuHooks {
  const hooks: RowMenuHooks = new Map()
  for (const source of sources) {
    if (!/function use\w*RowMenu\b/.test(source.text)) continue
    const file = parse(source)
    const scope = fileScope(file, hooks, state)
    for (const statement of file.statements) {
      if (!ts.isFunctionDeclaration(statement) || !statement.name || !/^use\w*RowMenu$/.test(statement.name.text) || !statement.body) continue
      const name = statement.name.text
      const body = statement.body.statements
      const last = body[body.length - 1]
      let earlyReturn = false
      for (const top of body.slice(0, -1)) {
        walk(top, (n) => {
          if (!ts.isReturnStatement(n)) return
          // A `return` of the hook itself, not of a function declared inside it.
          let inner = false
          for (let a: ts.Node = n.parent; a !== top.parent; a = a.parent) if (ts.isFunctionLike(a)) inner = true
          if (!inner) earlyReturn = true
        })
      }
      const returned = last && ts.isReturnStatement(last) && last.expression ? unwrap(last.expression) : undefined
      const hook: RowMenuHook | 'ambiguous' =
        returned && ts.isObjectLiteralExpression(returned) && !earlyReturn ? { scope, returned } : 'ambiguous'
      hooks.set(name, hooks.has(name) ? 'ambiguous' : hook)
    }
  }
  return hooks
}

function isCallTo(expr: ts.Expression, names: string[]): expr is ts.CallExpression {
  return ts.isCallExpression(expr) && ts.isIdentifier(expr.expression) && names.includes(expr.expression.text)
}

/** A call the scan does not read into: real work in a body, nothing in a condition. */
function trusted(mode: Mode): Ways {
  return mode === 'condition' ? NONE : WORK
}

/** Resolves `value` once at a time: a value met again while it is being resolved cannot be followed. */
function resolving(value: ts.Node, scope: FileScope, resolve: () => Ways): Ways {
  if (scope.resolving.has(value)) return UNRESOLVED
  scope.resolving.add(value)
  try {
    return resolve()
  } finally {
    scope.resolving.delete(value)
  }
}

function functionWays(fn: Callable, scope: FileScope, mode: Mode): Ways {
  const known = scope.memo[mode].get(fn)
  if (known) return known
  if (scope.pending[mode].has(fn)) {
    // Recursion adds nothing the first visit does not judge. A result that
    // leaned on an unfinished function is not memoised (below), so a second
    // handler entering the cycle elsewhere is judged in full.
    scope.cycleHits.push(fn)
    return NONE
  }
  scope.pending[mode].add(fn)
  const mark = scope.cycleHits.length
  let ways: Ways = NONE
  if (fn.body && ts.isBlock(fn.body)) {
    ways = distinct(runStatements(fn.body.statements, scope, mode).map((p) => way(p.effect, p.done === 'throw')))
  } else if (fn.body) {
    ways = expressionWays(fn.body, scope, mode)
  }
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
    if (paths.every((p) => p.done)) break
    const steps = statementPaths(statement, scope, mode) // once per statement, whatever the paths before it
    const next: Path[] = []
    for (const path of paths) {
      if (path.done) next.push(path)
      else next.push(...prefixed([way(path.effect)], steps))
    }
    paths = distinctPaths(next)
  }
  return paths
}

/** `paths`, each after one of `ways`; a way that threw ends there. */
function prefixed(ways: Ways, paths: Path[]): Path[] {
  return distinctPaths(
    ways.flatMap((w): Path[] =>
      w.threw ? [{ effect: w.effect, done: 'throw' }] : paths.map((p) => ({ effect: inSequence([w.effect, p.effect]), done: p.done })),
    ),
  )
}

/** Statements that only run on: each way of `ways`, as a path that goes on (or throws). */
function goOn(ways: Ways, done: Path['done'] = false): Path[] {
  return distinctPaths(ways.map((w) => ({ effect: w.effect, done: w.threw ? 'throw' : done })))
}

function statementPaths(statement: ts.Statement, scope: FileScope, mode: Mode): Path[] {
  if (ts.isBlock(statement)) return runStatements(statement.statements, scope, mode)
  if (ts.isExpressionStatement(statement)) return goOn(expressionWays(statement.expression, scope, mode))
  if (ts.isReturnStatement(statement)) {
    return goOn(statement.expression ? expressionWays(statement.expression, scope, mode) : NONE, 'return')
  }
  if (ts.isThrowStatement(statement)) {
    return expressionWays(statement.expression, scope, 'condition').map((w) => ({ effect: w.effect, done: 'throw' }))
  }
  if (ts.isBreakStatement(statement) && !statement.label) return [{ effect: 'none', done: 'break' }]
  if (ts.isIfStatement(statement)) {
    const branches = [
      ...statementPaths(statement.thenStatement, scope, mode),
      ...(statement.elseStatement ? statementPaths(statement.elseStatement, scope, mode) : goOn(NONE)),
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
    return prefixed(expressionWays(statement.expression, scope, 'condition'), distinctPaths(branches))
  }
  if (ts.isTryStatement(statement)) {
    const tried = runStatements(statement.tryBlock.statements, scope, mode)
    let ways = tried
    if (statement.catchClause) {
      // A catch starts from what the try had done when it threw — a `throw` the
      // scan saw, or any call (an import may throw), so from every way the try goes.
      const start = distinct(tried.map((p) => way(p.effect)))
      const caught = prefixed(start, runStatements(statement.catchClause.block.statements, scope, mode))
      ways = [...tried.filter((p) => p.done !== 'throw'), ...caught]
    }
    if (!statement.finallyBlock) return distinctPaths(ways)
    const cleanup = runStatements(statement.finallyBlock.statements, scope, mode)
    return distinctPaths(
      ways.flatMap((w) => cleanup.map((c) => ({ effect: inSequence([w.effect, c.effect]), done: c.done || w.done }))),
    )
  }
  if (ts.isVariableStatement(statement)) {
    const initializers = statement.declarationList.declarations.flatMap((d) => (d.initializer ? [d.initializer] : []))
    return goOn(thenAll(initializers.map((i) => expressionWays(i, scope, mode))))
  }
  if (
    ts.isFunctionDeclaration(statement) ||
    ts.isEmptyStatement(statement) ||
    ts.isTypeAliasDeclaration(statement) ||
    ts.isInterfaceDeclaration(statement)
  ) {
    return goOn(NONE)
  }
  // A loop, a label, …: not judged, so not passed — whether it runs on, or
  // leaves from inside by a `return` or a `throw` (out of a called function
  // too, where a `return` would leave only that function).
  return [
    { effect: 'unresolved', done: false },
    { effect: 'unresolved', done: 'return' },
    { effect: 'unresolved', done: 'throw' },
  ]
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
      return then(expressionWays(expr.left, scope, 'condition'), either(NONE, expressionWays(expr.right, scope, mode)))
    }
    const right = expressionWays(expr.right, scope, mode)
    if (isAssignment(operator)) return then(right, WORK)
    return then(expressionWays(expr.left, scope, mode), right)
  }
  if (ts.isCallExpression(expr)) return callWays(expr, scope, mode)
  if (ts.isNewExpression(expr) || ts.isTaggedTemplateExpression(expr)) return trusted(mode)
  if (ts.isPrefixUnaryExpression(expr) || ts.isPostfixUnaryExpression(expr)) {
    const operator = expr.operator
    return operator === ts.SyntaxKind.PlusPlusToken || operator === ts.SyntaxKind.MinusMinusToken
      ? WORK
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
            : NONE,
      ),
    )
  }
  return NONE // a name, a literal, a member read, a function not called
}

function callWays(call: ts.CallExpression, scope: FileScope, mode: Mode): Ways {
  if (isTabSwitchCall(call)) return [way('tab-switch')]
  const args = thenAll(call.arguments.map((a) => expressionWays(a, scope, mode)))
  const callee = unwrap(call.expression)
  let ways: Ways
  if (ts.isIdentifier(callee)) ways = nameCallWays(callee.text, scope, mode)
  else if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) ways = memberCallWays(callee, scope, mode)
  else if (ts.isArrowFunction(callee) || ts.isFunctionExpression(callee)) ways = functionWays(callee, scope, mode)
  else ways = UNRESOLVED // `make()()`: what is called is not in the file
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
  return scope.params.has(name) ? UNRESOLVED : trusted(mode)
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
  // A function literal is followed directly: functionWays handles recursion itself.
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionWays(expr, scope, mode)
  return resolving(expr, scope, () => {
    if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? localValueCallWays(expr.arguments[0], scope, mode) : UNRESOLVED
    const memoised = memoisedValue(expr)
    if (memoised && !objectLiteralOf(expr)) return localValueCallWays(memoised, scope, mode)
    if (ts.isIdentifier(expr)) return expr.text === 'undefined' ? NONE : nameCallWays(expr.text, scope, mode)
    if (ts.isConditionalExpression(expr)) {
      return either(localValueCallWays(expr.whenTrue, scope, mode), localValueCallWays(expr.whenFalse, scope, mode))
    }
    if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) return memberCallWays(expr, scope, mode)
    return trusted(mode)
  })
}

/** The object literal a local `const` holds, directly or as `useMemo(() => ({ … }))`'s value. */
function objectLiteralOf(value: ts.Node): ts.ObjectLiteralExpression | null {
  if (!ts.isExpression(value)) return null
  const expr = unwrap(value)
  if (ts.isObjectLiteralExpression(expr)) return expr
  const memoised = memoisedValue(expr)
  return memoised ? objectLiteralOf(memoised) : null
}

// Receivers the scan cannot read into whose members are treated as real work:
// a TanStack Query mutation (`mutate`, `mutateAsync`) and the query client
// (`invalidateQueries`). Trusted inside a body only — never as a handler value.
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
 * Judges the members of an object literal a member access can reach — one by
 * name, every one for a computed key. A spread in the object may supply or
 * override any member, so it cannot be followed.
 */
function membersWays(
  access: ts.PropertyAccessExpression | ts.ElementAccessExpression,
  object: ts.ObjectLiteralExpression,
  judgeMember: (member: ts.ObjectLiteralElementLike) => Ways,
): Ways {
  const name = memberName(access)
  const members = object.properties.filter((p) => !ts.isSpreadAssignment(p) && (name === null || propertyName(p) === name))
  const results = members.map(judgeMember)
  if (object.properties.some(ts.isSpreadAssignment) || members.length === 0) results.push(UNRESOLVED)
  return either(...results)
}

/**
 * The row-menu hook a local holds (`const menu = useLocationRowMenu(…)`):
 * only through the file's own import of it, and only when one file declares
 * it — else `ambiguous`, which cannot be followed.
 */
function rowMenuHookOf(target: ts.Node, scope: FileScope): RowMenuHook | 'ambiguous' | undefined {
  if (!ts.isExpression(target)) return undefined
  const call = unwrap(target)
  if (!ts.isCallExpression(call) || !ts.isIdentifier(call.expression)) return undefined
  const name = call.expression.text
  if (!scope.imports.has(name)) return undefined
  return scope.rowMenuHooks.get(name)
}

/**
 * Calling through a member inside a body — `startAction.allocate()`,
 * `handlers[kind]()`, `menu.requestDeactivation(location)`. Followed into a
 * local object literal or a `use…RowMenu` hook's returned object; a local the
 * scan cannot read into, or a parameter, cannot be followed; an import or a
 * global (`api.post`, `navigator.clipboard`) is trusted, as a call to an
 * imported function is.
 */
function memberCallWays(access: ts.PropertyAccessExpression | ts.ElementAccessExpression, scope: FileScope, mode: Mode): Ways {
  const receiver = unwrap(access.expression)
  if (!ts.isIdentifier(receiver)) {
    const root = rootName(receiver)
    if (root === null) return UNRESOLVED
    return scope.decls.has(root) || scope.params.has(root) ? UNRESOLVED : trusted(mode)
  }
  const targets = scope.decls.get(receiver.text)
  if (!targets) return scope.params.has(receiver.text) ? UNRESOLVED : trusted(mode)
  return either(
    ...targets.map((target): Ways => {
      if (ts.isExpression(target) && WRITING_HOOKS.some((hook) => isCallTo(unwrap(target), [hook]))) return trusted(mode)
      const hook = rowMenuHookOf(target, scope)
      if (hook === 'ambiguous') return UNRESOLVED
      const object = hook?.returned ?? objectLiteralOf(target)
      if (!object) return UNRESOLVED
      const where = hook?.scope ?? scope
      return membersWays(access, object, (member) => {
        if (ts.isPropertyAssignment(member)) return localValueCallWays(member.initializer, where, mode)
        if (ts.isShorthandPropertyAssignment(member)) return nameCallWays(member.name.text, where, mode)
        if (ts.isMethodDeclaration(member)) return functionWays(member, where, mode)
        return UNRESOLVED
      })
    }),
  )
}

/**
 * The ways of a handler value — what a click calls. KAN-468's rule holds for
 * the value itself: it must resolve, in the file (or a `use…RowMenu` hook), to
 * a function the scan can read. A name or member that is a prop, an import or
 * a local holding anything else (`useStartAction()`'s result,
 * `importedHandler`) cannot be followed. Only inside the function it resolves
 * to are imports trusted.
 */
function handlerWays(value: ts.Expression, scope: FileScope): Ways {
  const expr = unwrap(value)
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return functionWays(expr, scope, 'body')
  return resolving(expr, scope, () => {
    if (ts.isIdentifier(expr)) {
      if (expr.text === 'undefined') return NONE
      const targets = scope.decls.get(expr.text)
      if (!targets) return UNRESOLVED
      return either(...targets.map((t) => (ts.isFunctionDeclaration(t) ? functionWays(t, scope, 'body') : handlerWays(t as ts.Expression, scope))))
    }
    if (ts.isConditionalExpression(expr)) return either(handlerWays(expr.whenTrue, scope), handlerWays(expr.whenFalse, scope))
    if (isCallTo(expr, ['useCallback'])) return expr.arguments[0] ? handlerWays(expr.arguments[0], scope) : UNRESOLVED
    const memoised = memoisedValue(expr)
    if (memoised) return handlerWays(memoised, scope)
    if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) {
      const receiver = unwrap(expr.expression)
      const targets = ts.isIdentifier(receiver) ? scope.decls.get(receiver.text) : undefined
      if (!targets) return UNRESOLVED
      return either(
        ...targets.map((target): Ways => {
          const hook = rowMenuHookOf(target, scope)
          if (hook === 'ambiguous') return UNRESOLVED
          const object = hook?.returned ?? objectLiteralOf(target)
          if (!object) return UNRESOLVED
          const where = hook?.scope ?? scope
          return membersWays(expr, object, (member) => {
            if (ts.isPropertyAssignment(member)) return handlerWays(member.initializer, where)
            if (ts.isShorthandPropertyAssignment(member)) return handlerWays(member.name, where)
            if (ts.isMethodDeclaration(member)) return functionWays(member, where, 'body')
            return UNRESOLVED
          })
        }),
      )
    }
    return UNRESOLVED
  })
}

/** Judges a handler value: `ok`, a pure tab switch, or something the scan cannot follow. */
function judge(value: ts.Expression, scope: FileScope): Verdict {
  const effect = worst(handlerWays(value, scope).map((w) => w.effect))
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
  const state = judgeState()
  const rowMenuHooks = rowMenuHooksOf(pages, state)
  for (const source of pages) {
    if (!source.text.includes('<DetailHeader')) continue
    const file = parse(source)
    const scope = fileScope(file, rowMenuHooks, state)
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

  // KAN-512 reviews: the rules of the path judge, each with a case meant to
  // turn green if the rule is removed (a mutation run checks it), and the
  // KAN-468 handler-value rule kept.
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

    // KAN-512 review, round 2.
    // A loop is not judged — also when it can leave the handler and real work follows it.
    expect(
      verdict('', "() => { for (const issue of issues) { if (issue.blocking) { setActiveTab(issue.tab); return } } setAllocating(true) }"),
    ).toBe('cannot be followed')
    // A throw out of a called local function ends the path there, as an inline one does.
    expect(
      verdict(
        "const validate = () => { if (!valid) throw 'invalid' }",
        "async () => { try { validate(); await api.post('/x') } catch { setActiveTab('identity') } }",
      ),
    ).toBe('tab switch')
    expect(verdict("const fail = () => { setActiveTab('identity'); throw 'stop' }", '() => { if (bad) fail(); setAllocating(true) }')).toBe(
      'tab switch',
    )
    // A throw's value is a condition: constructing the error is no work.
    expect(verdict('', "() => { if (editing) { setActiveTab('identity'); throw new Error('stop') } setAllocating(true) }")).toBe('tab switch')
    // A switch without a default can match no case and run on past it.
    expect(verdict('', "() => { switch (kind) { case 'a': setAllocating(true); return } setActiveTab('identity') }")).toBe('tab switch')
    // A returned value is what the handler does.
    expect(verdict('', "() => { return setActiveTab('identity') }")).toBe('tab switch')
    // A called useCallback local is followed.
    expect(verdict("const go = useCallback(() => setActiveTab('identity'), [])", '() => go()')).toBe('tab switch')
    // A computed key judges every member, the tab switch listed last included.
    expect(
      verdict("const actions = { odometer: () => setFormToFocus('odometer'), allocate: () => setActiveTab('identity') }", '() => actions[kind]()'),
    ).toBe('tab switch')
    // Through a local object: a nested member, a missing member and a spread cannot be followed.
    expect(verdict("const startAction = { inner: { allocate: () => setAllocating(true) } }", '() => startAction.inner.allocate()')).toBe(
      'cannot be followed',
    )
    expect(verdict("const actions = { allocate: () => setAllocating(true) }", '() => actions.missing()')).toBe('cannot be followed')
    expect(verdict("const base = makeActions(); const actions = { allocate: () => setAllocating(true), ...base }", '() => actions.allocate()')).toBe(
      'cannot be followed',
    )
    // A name cycle cannot be followed (never a stack overflow).
    expect(verdict('const p = q; const q = p', 'p')).toBe('cannot be followed')
    expect(verdict('const p = q; const q = p', '() => p()')).toBe('cannot be followed')
    // Deep nesting is judged in linear time: every statement once, whatever the paths before it.
    const nest = (depth: number): string =>
      depth === 0
        ? 'setAllocating(true)'
        : `if (a${depth}) setFormToFocus('f${depth}'); if (c${depth}) { ${nest(depth - 1)} } else { ${nest(depth - 1)} }`
    expect(verdict('', `() => { ${nest(14)} }`)).toBe('ok')

    // A function judged as a predicate and the same function called in a body are two judgements.
    const twoModes = `export function A() {
      const go = () => api.post('/x')
      const first = buildOldRowMenu(t, { onAllocate: () => { if (go()) return; setActiveTab('identity') } })
      const second = buildOldRowMenu(t, { onAllocate: () => { go(); setActiveTab('identity') } })
      return <DetailHeader primaryAction={first.primary} />
    }`
    expect(noOpPrimaries([{ path: 'pages/P.tsx', text: twoModes }], builders)).toEqual(['pages/P.tsx:3 onAllocate (primary of buildOldRowMenu)'])

    // A `use…RowMenu` hook (ADR-061's one definition) is read across files, like a builder.
    const hook: Source = {
      path: 'components/useThingRowMenu.tsx',
      text: `export function useThingRowMenu() {
        const goIdentity = () => setActiveTab('identity')
        const deactivate = useCallback((thing) => { setError(null); setPending(thing) }, [])
        return { goIdentity, deactivate, groups: (thing) => ({}) }
      }`,
    }
    const usesHook = `import { useThingRowMenu } from '../components/useThingRowMenu'
    export function A() {
      const menu = useThingRowMenu()
      const a = buildOldRowMenu(t, { onAllocate: () => menu.deactivate(thing) })
      const b = buildOldRowMenu(t, { onAllocate: () => menu.goIdentity() })
      const c = buildOldRowMenu(t, { onAllocate: menu.goIdentity })
      const d = buildOldRowMenu(t, { onAllocate: () => menu.missing() })
      return <DetailHeader primaryAction={a.primary} />
    }`
    expect(noOpPrimaries([hook, { path: 'pages/P.tsx', text: usesHook }], builders)).toEqual([
      'pages/P.tsx:5 onAllocate (primary of buildOldRowMenu)',
      'pages/P.tsx:6 onAllocate (primary of buildOldRowMenu)',
      'pages/P.tsx:7 onAllocate (primary of buildOldRowMenu) (cannot be followed)',
    ])

    // KAN-512 review, round 3.
    // A loop in a called function that leaves by `throw` after a tab switch: the work after the call never runs.
    expect(
      verdict(
        "const validate = () => { for (const issue of issues) { if (issue.blocking) { setActiveTab(issue.tab); throw 'stop' } } }",
        '() => { validate(); setAllocating(true) }',
      ),
    ).toBe('cannot be followed')
    // … and one inside a `try` that leaves by `return`: a catch never sees that path.
    expect(
      verdict('', "() => { try { for (const i of issues) { if (i.blocking) { setActiveTab(i.tab); return } } } catch { setError(true) } setAllocating(true) }"),
    ).toBe('cannot be followed')
    // What runs after a throw does not run: in an argument list, and out of an `if` condition.
    const fail = "const fail = () => { setActiveTab('identity'); throw 'stop' }"
    expect(verdict(fail, '() => { noop(fail(), setAllocating(true)) }')).toBe('tab switch')
    expect(verdict(fail, "() => { if (fail()) setAllocating(true); else setFormToFocus('x') }")).toBe('tab switch')
    // Arguments and initialisers run.
    expect(verdict('const pass = (x) => x', "() => pass(setActiveTab('identity'))")).toBe('tab switch')
    expect(verdict('', "() => { const tab = setActiveTab('identity') }")).toBe('tab switch')
    // What is called is followed: an immediately invoked arrow, a memoised function, a local `?:`, a member alias.
    expect(verdict('', "() => (() => setActiveTab('identity'))()")).toBe('tab switch')
    expect(verdict("const go = useMemo(() => () => setActiveTab('identity'), [])", '() => go()')).toBe('tab switch')
    expect(verdict("const go = ready ? () => setAllocating(true) : () => setActiveTab('identity')", '() => go()')).toBe('tab switch')
    expect(verdict("const actions = { allocate: () => setActiveTab('identity') }; const f = actions.allocate", '() => f()')).toBe('tab switch')
    // … and a call whose callee is a call cannot be followed.
    expect(verdict('', '() => make()()')).toBe('cannot be followed')
    expect(verdict('', '() => make().allocate()')).toBe('cannot be followed')
    // A `?:` handler value is judged on both sides.
    const both = "const good = () => setAllocating(true); const bad = () => setActiveTab('identity')"
    expect(verdict(both, 'ready ? good : bad')).toBe('tab switch')
    expect(verdict(both, 'ready ? bad : good')).toBe('tab switch')
    // A handler value that is a member of a prop or an import cannot be followed (KAN-468).
    expect(verdict('', 'props.onAllocate')).toBe('cannot be followed')
    expect(verdict('', 'api.allocate')).toBe('cannot be followed')

    // A row-menu hook is followed only through the page's import of it, only when one file declares
    // it, only when it is a single `return { … }` at its end, and only a `use…RowMenu`.
    const thingPage = (call: string, imported = true) => ({
      path: 'pages/P.tsx',
      text: `${imported ? "import { useThingRowMenu, useThing } from '../components/useThingRowMenu'\n" : ''}export function A() {
        const menu = ${call}
        const a = buildOldRowMenu(t, { onAllocate: () => menu.go() })
        return <DetailHeader primaryAction={a.primary} />
      }`,
    })
    const working: Source = { path: 'components/useThingRowMenu.tsx', text: 'export function useThingRowMenu() { return { go: () => setAllocating(true) } }' }
    const tabbing: Source = { path: 'components/other/useThingRowMenu.tsx', text: "export function useThingRowMenu() { return { go: () => setActiveTab('identity') } }" }
    const followed = (sources: Source[]) => noOpPrimaries(sources, builders).map((o) => o.replace(/^.* \(primary of buildOldRowMenu\)/, '').trim() || 'tab switch')
    expect(followed([working, thingPage('useThingRowMenu()')])).toEqual([])
    expect(followed([working, thingPage('useThingRowMenu()', false)])).toEqual(['(cannot be followed)'])
    expect(followed([working, tabbing, thingPage('useThingRowMenu()')])).toEqual(['(cannot be followed)'])
    const early: Source = {
      path: 'components/useThingRowMenu.tsx',
      text: "export function useThingRowMenu() { if (legacy) { return { go: () => setActiveTab('identity') } } return { go: () => setAllocating(true) } }",
    }
    expect(followed([early, thingPage('useThingRowMenu()')])).toEqual(['(cannot be followed)'])
    const plainHook: Source = { path: 'components/useThingRowMenu.tsx', text: 'export function useThing() { return { go: () => setAllocating(true) } }' }
    expect(followed([plainHook, thingPage('useThing()')])).toEqual(['(cannot be followed)'])
    // Two hooks calling each other: one cycle, whichever primary is judged first.
    const hookA: Source = {
      path: 'components/useARowMenu.tsx',
      text: `import { useBRowMenu } from './useBRowMenu'
      export function useARowMenu() {
        const b = useBRowMenu()
        const f = () => { if (k) { b.g() } else { setActiveTab('identity') } }
        return { f }
      }`,
    }
    const hookB: Source = {
      path: 'components/useBRowMenu.tsx',
      text: `import { useARowMenu } from './useARowMenu'
      export function useBRowMenu() {
        const a = useARowMenu()
        const g = () => { if (j) { a.f() } else { void api.post('/x') } }
        return { g }
      }`,
    }
    const pair = (first: string, second: string): Source => ({
      path: 'pages/P.tsx',
      text: `import { useARowMenu } from '../components/useARowMenu'
      import { useBRowMenu } from '../components/useBRowMenu'
      export function A() {
        const ma = useARowMenu()
        const mb = useBRowMenu()
        const x = buildOldRowMenu(t, { onAllocate: () => ${first} })
        const y = buildOldRowMenu(t, { onAllocate: () => ${second} })
        return <DetailHeader primaryAction={x.primary} />
      }`,
    })
    expect(noOpPrimaries([hookA, hookB, pair('ma.f()', 'mb.g()')], builders)).toHaveLength(2)
    expect(noOpPrimaries([hookA, hookB, pair('mb.g()', 'ma.f()')], builders)).toHaveLength(2)

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
