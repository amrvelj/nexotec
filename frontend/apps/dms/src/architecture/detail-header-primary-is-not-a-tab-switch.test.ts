/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, posix, relative } from 'node:path'
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
//   through it (`menu.toggleActive(location)`) is followed into it;
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
// through the page's import of the file that declares it, when one file
// declares that name and no local shadows it.
// KAN-518: a name bound by destructuring is followed where the scan can read
// it — a member of a local object or of a row-menu hook — and a call to it
// cannot be followed otherwise (`const { allocate } = useStartActions()`,
// `const [, start] = useDispatcher()`, `const { onAllocateProp } = props`),
// except the values `TRUSTED_DESTRUCTURED` names with a reason (a `useState`
// or `useReducer` setter, the `useSearchParams` setter, `t`, a mutation's
// `mutate`), each only from its own package. A `useSearchParams` setter naming
// a tab is a tab switch under any spelling (`setParams`). A call to a local
// holding a hook's result (`const startAction = useStartAction()`, also
// `Hooks.useStartAction()` and `use(Context)`) cannot be followed either,
// except a hook `TRUSTED_HOOK_RESULTS` names (`useNavigate`). A function handed
// to an import or a global — a literal (`startTransition(() => …)`) or a name
// the file binds only to functions (`setTimeout(goOverview)`) — is judged as
// if it ran, the call itself counting as nothing. A `let` handler, destructured
// or not, is judged by every value assigned to it.
// Names are judged file-wide, not by lexical scope: every declaration,
// destructuring and parameter of one spelling in the file is a possible
// meaning, and the strictest decides (a prop `go` in one component and
// `const [x, go] = useState()` in another make `go()` "cannot be followed").
// Known misses: a callback inside an object handed to an import
// (`modals.openConfirmModal({ onConfirm: () => … })`) is not judged — opening
// the dialog is the work; a destructuring assignment to an existing `let`
// (`({ go } = useStartActions())`) is not read as a new value of `go`.

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

/** Every name a binding binds, destructured ones included. */
function boundNames(name: ts.BindingName): string[] {
  if (ts.isIdentifier(name)) return [name.text]
  return name.elements.flatMap((element) => (ts.isBindingElement(element) ? boundNames(element.name) : []))
}

const REASSIGNING = new Set([
  ts.SyntaxKind.EqualsToken,
  ts.SyntaxKind.BarBarEqualsToken,
  ts.SyntaxKind.AmpersandAmpersandEqualsToken,
  ts.SyntaxKind.QuestionQuestionEqualsToken,
])

/**
 * Every `const name = …` and `function name() {}` in the file, by name — and
 * every value later assigned to a `let`/`var` name (KAN-518), so a handler
 * reassigned after its declaration is judged by each value it can hold.
 */
function declarations(file: ts.SourceFile): Map<string, ts.Node[]> {
  const found = new Map<string, ts.Node[]>()
  const add = (name: string, node: ts.Node) => found.set(name, [...(found.get(name) ?? []), node])
  const mutable = new Set<string>()
  walk(file, (n) => {
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer) add(n.name.text, n.initializer)
    if (ts.isFunctionDeclaration(n) && n.name) add(n.name.text, n)
    if (ts.isVariableDeclarationList(n) && !(n.flags & ts.NodeFlags.Const)) {
      for (const d of n.declarations) for (const name of boundNames(d.name)) mutable.add(name)
    }
  })
  walk(file, (n) => {
    if (ts.isBinaryExpression(n) && REASSIGNING.has(n.operatorToken.kind) && ts.isIdentifier(n.left) && mutable.has(n.left.text)) {
      add(n.left.text, n.right)
    }
  })
  return found
}

/** One step from a destructured value to the name it binds: an array element, or a property (`null`: a computed key). */
type BindingStep = { index: number } | { property: string | null }

/** A name bound by destructuring (`const { a } = x`, `const [, b] = useY()`): where its value comes from. */
interface DestructuredBinding {
  source: ts.Expression
  path: BindingStep[]
  /** The `= fallback` the binding names, if any. */
  fallback?: ts.Expression
}

/** Every name a `const`/`let` destructuring binds, by name — parameters are not here (they are `parameterNames`). */
function destructuredBindings(file: ts.SourceFile): Map<string, DestructuredBinding[]> {
  const found = new Map<string, DestructuredBinding[]>()
  const bind = (name: ts.BindingName, source: ts.Expression, path: BindingStep[], fallback?: ts.Expression) => {
    if (ts.isIdentifier(name)) {
      found.set(name.text, [...(found.get(name.text) ?? []), { source, path, fallback }])
      return
    }
    name.elements.forEach((element, index) => {
      if (!ts.isBindingElement(element)) return // an array hole
      let step: BindingStep
      if (ts.isArrayBindingPattern(name)) step = { index }
      else {
        const key = element.propertyName ?? (ts.isIdentifier(element.name) ? element.name : undefined)
        step = { property: key && (ts.isIdentifier(key) || ts.isStringLiteral(key)) ? key.text : null }
      }
      // A rest element (`...rest`) holds what is left: no one member, so a computed step.
      if (element.dotDotDotToken) step = { property: null }
      bind(element.name, source, [...path, step], element.initializer)
    })
  }
  walk(file, (n) => {
    if (ts.isVariableDeclaration(n) && !ts.isIdentifier(n.name) && n.initializer) bind(n.name, n.initializer, [])
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
// (`scope.path` is the file that declares it: a page's import must name that file.)

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
  resolving: Set<object>
}

function judgeState(): JudgeState {
  return { memo: { body: new Map(), condition: new Map() }, pending: { body: new Set(), condition: new Set() }, cycleHits: [], resolving: new Set() }
}

/** What the scan knows of one file. */
interface FileScope extends JudgeState {
  /** The file's path under `src/`, against which its relative imports resolve. */
  path: string
  decls: Map<string, ts.Node[]>
  destructured: Map<string, DestructuredBinding[]>
  params: Set<string>
  /** Names the file imports, with the name exported and the module: a row-menu hook is followed only through its import. */
  imports: Map<string, ImportedName>
  rowMenuHooks: RowMenuHooks
}

interface ImportedName {
  exported: string
  from: string
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

/** The names a file's `import` declarations bind: local name → the name exported and the module. */
function importedNames(file: ts.SourceFile): Map<string, ImportedName> {
  const names = new Map<string, ImportedName>()
  for (const statement of file.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue
    const clause = statement.importClause
    if (!clause) continue
    const from = statement.moduleSpecifier.text
    if (clause.name) names.set(clause.name.text, { exported: 'default', from })
    const bindings = clause.namedBindings
    if (bindings && ts.isNamespaceImport(bindings)) names.set(bindings.name.text, { exported: '*', from })
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) names.set(element.name.text, { exported: (element.propertyName ?? element.name).text, from })
    }
  }
  return names
}

/** Whether `specifier`, imported by the file at `importer`, is the module at `target` (paths under `src/`). */
function importResolvesTo(importer: string, specifier: string, target: string): boolean {
  if (!specifier.startsWith('.')) return false // a package's export of the same name is not the app's hook
  const resolved = posix.normalize(posix.join(posix.dirname(importer.split('\\').join('/')), specifier))
  const module = target.split('\\').join('/').replace(/\.tsx?$/, '')
  return resolved === module || `${resolved}/index` === module
}

function fileScope(file: ts.SourceFile, path: string, rowMenuHooks: RowMenuHooks = new Map(), state: JudgeState = judgeState()): FileScope {
  return {
    ...state,
    path,
    decls: declarations(file),
    destructured: destructuredBindings(file),
    params: parameterNames(file),
    imports: importedNames(file),
    rowMenuHooks,
  }
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
    const scope = fileScope(file, source.path, hooks, state)
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

/** The name a call calls: `useX(…)` → `useX`, and through a namespace `React.useX(…)` → `useX` (KAN-518). */
function calleeName(call: ts.CallExpression): string | null {
  const callee = unwrap(call.expression)
  if (ts.isIdentifier(callee)) return callee.text
  if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(unwrap(callee.expression))) return callee.name.text
  return null
}

/** The namespace a call goes through (`React` in `React.useState()`), else null. */
function calleeNamespace(call: ts.CallExpression): string | null {
  const callee = unwrap(call.expression)
  if (!ts.isPropertyAccessExpression(callee)) return null
  const receiver = unwrap(callee.expression)
  return ts.isIdentifier(receiver) ? receiver.text : null
}

function isCallTo(expr: ts.Expression, names: string[]): expr is ts.CallExpression {
  if (!ts.isCallExpression(expr)) return false
  const name = calleeName(expr)
  return name !== null && names.includes(name)
}

/** A call the scan does not read into: real work in a body, nothing in a condition. */
function trusted(mode: Mode): Ways {
  return mode === 'condition' ? NONE : WORK
}

/** Resolves `value` once at a time: a value met again while it is being resolved cannot be followed. */
function resolving(value: object, scope: FileScope, resolve: () => Ways): Ways {
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

/** A name the file neither declares, destructures nor takes as a parameter: an import or a global. */
function isOpaqueName(name: string, scope: FileScope): boolean {
  return !scope.decls.has(name) && !scope.destructured.has(name) && !scope.params.has(name)
}

/** A callee the scan does not read into because it is an import or a global, or a member reached through one (`api.post`). */
function isOpaqueCallee(callee: ts.Expression, scope: FileScope): boolean {
  if (ts.isIdentifier(callee)) return isOpaqueName(callee.text, scope)
  if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) {
    const root = rootName(callee.expression)
    return root !== null && isOpaqueName(root, scope)
  }
  return false
}

/**
 * A function handed to a call: a function literal, or a name the file binds
 * only to functions (`startTransition(goOverview)`) — never a value that may
 * be data.
 */
function handedFunction(arg: ts.Expression, scope: FileScope): Callable | ts.Identifier | null {
  const expr = unwrap(arg)
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) return expr
  if (!ts.isIdentifier(expr) || scope.destructured.has(expr.text) || scope.params.has(expr.text)) return null
  const targets = scope.decls.get(expr.text)
  if (!targets || targets.length === 0) return null
  const isFunction = (t: ts.Node) =>
    ts.isFunctionDeclaration(t) || ts.isArrowFunction(t) || ts.isFunctionExpression(t) || (ts.isExpression(t) && isCallTo(unwrap(t), ['useCallback']))
  return targets.every(isFunction) ? expr : null
}

/** Whether `name` is the second element of `useSearchParams()` under any spelling (`const [p, setParams] = useSearchParams()`). */
function isSearchParamsSetter(name: string, scope: FileScope): boolean {
  return (scope.destructured.get(name) ?? []).some((b) => {
    const source = unwrap(b.source)
    return ts.isCallExpression(source) && calleeName(source) === 'useSearchParams' && isElement(b.path, 1)
  })
}

function callWays(call: ts.CallExpression, scope: FileScope, mode: Mode): Ways {
  if (isTabSwitchCall(call)) return [way('tab-switch')]
  const callee = unwrap(call.expression)
  // KAN-518: the URL setter under another name (`setParams`) switches a tab
  // the way `setSearchParams` does (ADR-056 keeps the tab in the URL).
  if (ts.isIdentifier(callee) && isSearchParamsSetter(callee.text, scope) && call.arguments.some((a) => /\btab\b/.test(a.getText()))) {
    return [way('tab-switch')]
  }
  // KAN-518: a function handed to an import or a global (`startTransition(()
  // => …)`, `setTimeout(goOverview)`) is judged as if it ran, and the call
  // itself counts as nothing: what it does is the callback.
  const handed = call.arguments.map((a) => handedFunction(a, scope))
  if (handed.some((h) => h !== null) && isOpaqueCallee(callee, scope)) {
    return thenAll(
      call.arguments.map((a, i) => {
        const fn = handed[i]
        if (fn === null) return expressionWays(a, scope, mode)
        return ts.isIdentifier(fn) ? nameCallWays(fn.text, scope, mode) : functionWays(fn, scope, mode)
      }),
    )
  }
  const args = thenAll(call.arguments.map((a) => expressionWays(a, scope, mode)))
  let ways: Ways
  if (ts.isIdentifier(callee)) ways = nameCallWays(callee.text, scope, mode)
  else if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) ways = memberCallWays(callee, scope, mode)
  else if (ts.isArrowFunction(callee) || ts.isFunctionExpression(callee)) ways = functionWays(callee, scope, mode)
  else ways = UNRESOLVED // `make()()`: what is called is not in the file
  return then(args, ways)
}

/**
 * Calling a bare name: a local function is followed, and so is a name
 * destructured from what the scan can read; a parameter (a prop) cannot be;
 * an import or a global is trusted.
 */
function nameCallWays(name: string, scope: FileScope, mode: Mode): Ways {
  const targets = scope.decls.get(name) ?? []
  const bound = scope.destructured.get(name) ?? []
  if (targets.length > 0 || bound.length > 0) {
    return either(
      ...targets.map((t) => (ts.isFunctionDeclaration(t) ? functionWays(t, scope, mode) : localValueCallWays(t as ts.Expression, scope, mode))),
      ...bound.map((b) => destructuredCallWays(b, scope, mode)),
      // Names are judged file-wide: a parameter of the same name elsewhere
      // in the file may be what this call means, and a prop cannot be followed.
      ...(scope.params.has(name) ? [UNRESOLVED] : []),
    )
  }
  return scope.params.has(name) ? UNRESOLVED : trusted(mode)
}

/**
 * KAN-518 — the destructured values a handler body may call although the
 * scan cannot read into the hook that returns them, each with why. Anything
 * else destructured from a call's result cannot be followed.
 */
interface TrustedDestructured {
  /** The modules the hook must be imported from (a namesake from elsewhere is not it). */
  from: string[]
  calls: (path: BindingStep[]) => boolean
  effect: 'work' | 'none'
  why: string
}

const REACT = ['react']
const ROUTER = ['react-router-dom', 'react-router']

const TRUSTED_DESTRUCTURED = new Map<string, TrustedDestructured>([
  ['useState', { from: REACT, calls: (path) => isElement(path, 1), effect: 'work', why: 'the setter (second element) writes React state' }],
  ['useReducer', { from: REACT, calls: (path) => isElement(path, 1), effect: 'work', why: 'dispatch (second element) runs the reducer' }],
  [
    'useSearchParams',
    {
      from: ROUTER,
      calls: (path) => isElement(path, 1),
      effect: 'work',
      why: 'the setter (second element) writes the URL; one naming a tab, under any spelling, is a tab switch (isSearchParamsSetter, before this applies)',
    },
  ],
  ['useTranslation', { from: ['react-i18next'], calls: (path) => isProperty(path, 't'), effect: 'none', why: 't looks a message up: no work, never a tab switch' }],
  [
    'useMutation',
    {
      from: ['@tanstack/react-query'],
      calls: (path) => isProperty(path, 'mutate') || isProperty(path, 'mutateAsync'),
      effect: 'work',
      why: 'a mutation writes — WRITING_HOOKS trusts the same call through `mutation.mutate()`',
    },
  ],
])

/**
 * KAN-518 — the hooks whose result a handler body may call, each with why.
 * A call to a local holding any other hook's result cannot be followed.
 */
const TRUSTED_HOOK_RESULTS = new Map<string, { from: string[]; why: string }>([
  ['useNavigate', { from: ROUTER, why: 'navigate(…) changes the route: real work' }],
])

/**
 * Whether `call` calls the hook `hook` of one of `modules`: imported from one
 * of them (by name, or through a namespace import like `React`), or a name
 * the file neither imports nor binds (a global, as in the self-tests). A
 * local or another module's namesake is not it.
 */
function callsHookFrom(call: ts.CallExpression, hook: string, modules: string[], scope: FileScope): boolean {
  if (calleeName(call) !== hook) return false
  const namespace = calleeNamespace(call)
  const local = namespace ?? hook
  const imported = scope.imports.get(local)
  if (imported) return modules.includes(imported.from) && (namespace !== null || imported.exported === hook)
  return isOpaqueName(local, scope)
}

function isElement(path: BindingStep[], index: number): boolean {
  return path.length === 1 && 'index' in path[0] && path[0].index === index
}

function isProperty(path: BindingStep[], name: string): boolean {
  return path.length === 1 && 'property' in path[0] && path[0].property === name
}

/** The one property name a binding takes straight from its source (`const { allocate } = x`), else null. */
function soleProperty(path: BindingStep[]): string | null {
  return path.length === 1 && 'property' in path[0] ? path[0].property : null
}

/**
 * Calling a name bound by destructuring. From a local object (or a row-menu
 * hook): judged as the member it names, `const { allocate } = actions` as
 * `actions.allocate()`. From an import or a global: trusted, as a call through
 * one is. From a parameter (`const { onAllocate } = props`), from any other
 * call's result (`const { allocate } = useStartActions()`, `const [, start] =
 * useDispatcher()`) and through a nested or computed step: cannot be followed
 * — unless `TRUSTED_DESTRUCTURED` names it.
 */
function destructuredCallWays(binding: DestructuredBinding, scope: FileScope, mode: Mode): Ways {
  // A binding met again while it is being judged (`const { allocate } = actions`
  // beside `const actions = { allocate }` in another component) cannot be followed.
  return resolving(binding, scope, () => {
    const source = unwrap(binding.source)
    const property = soleProperty(binding.path)
    let ways: Ways = UNRESOLVED
    if (ts.isCallExpression(source)) {
      const name = calleeName(source)
      const rule = name !== null ? TRUSTED_DESTRUCTURED.get(name) : undefined
      const hook = rowMenuHookOf(source, scope)
      if (rule && name !== null && callsHookFrom(source, name, rule.from, scope) && rule.calls(binding.path)) {
        ways = rule.effect === 'none' ? NONE : trusted(mode)
      } else if (hook && hook !== 'ambiguous' && property !== null && ts.isIdentifier(source.expression)) {
        ways = membersWays(memberAccess(source.expression.text, property), hook.returned, memberJudge(hook.scope, mode))
      }
    } else if (ts.isIdentifier(source) && property !== null) {
      ways = memberCallWays(memberAccess(source.text, property), scope, mode)
    } else if (ts.isIdentifier(source) || ts.isPropertyAccessExpression(source) || ts.isElementAccessExpression(source)) {
      const root = rootName(source)
      ways = root !== null && isOpaqueName(root, scope) ? trusted(mode) : UNRESOLVED
    }
    return binding.fallback ? either(ways, localValueCallWays(binding.fallback, scope, mode)) : ways
  })
}

/** `receiver.property`, built so a destructured name is judged as the member access it stands for. */
function memberAccess(receiver: string, property: string): ts.PropertyAccessExpression {
  return ts.factory.createPropertyAccessExpression(ts.factory.createIdentifier(receiver), property)
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

/** Calling a local `const` by its initializer, inside a body. A value the scan cannot read is trusted, as before KAN-512 — but not a hook's result (KAN-518). */
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
    // KAN-518: a hook's result is not in the file (`const startAction =
    // useStartAction(); startAction('allocate')` is KAN-503's dispatcher moved
    // into a hook), unless the hook is named with a reason.
    const hook = ts.isCallExpression(expr) ? calleeName(expr) : null
    if (ts.isCallExpression(expr) && hook !== null && /^use(?:[A-Z0-9]|$)/.test(hook)) {
      const rule = TRUSTED_HOOK_RESULTS.get(hook)
      return rule && callsHookFrom(expr, hook, rule.from, scope) ? trusted(mode) : UNRESOLVED
    }
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
 * only through the file's own import of the module that declares it (KAN-518:
 * a package's export of the same name is not it), and only when one file
 * declares it and no local of the file shadows the import — else `ambiguous`,
 * which cannot be followed.
 */
function rowMenuHookOf(target: ts.Node, scope: FileScope): RowMenuHook | 'ambiguous' | undefined {
  if (!ts.isExpression(target)) return undefined
  const call = unwrap(target)
  if (!ts.isCallExpression(call) || !ts.isIdentifier(call.expression)) return undefined
  const name = call.expression.text
  const imported = scope.imports.get(name)
  if (!imported) return undefined
  const hook = scope.rowMenuHooks.get(imported.exported)
  if (!hook) return undefined
  if (scope.decls.has(name) || scope.destructured.has(name)) return 'ambiguous'
  if (hook === 'ambiguous') return hook
  return importResolvesTo(scope.path, imported.from, hook.scope.path) ? hook : undefined
}

/**
 * Calling through a member inside a body — `startAction.allocate()`,
 * `handlers[kind]()`, `menu.toggleActive(location)`. Followed into a
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
    return isOpaqueName(root, scope) ? trusted(mode) : UNRESOLVED
  }
  const targets = scope.decls.get(receiver.text) ?? []
  // KAN-518: a destructured receiver (`const { menu } = props`) holds a value the scan does not read into.
  const destructured = scope.destructured.has(receiver.text) ? [UNRESOLVED] : []
  if (targets.length === 0 && destructured.length === 0) return scope.params.has(receiver.text) ? UNRESOLVED : trusted(mode)
  return either(
    ...destructured,
    ...targets.map((target): Ways => {
      if (ts.isExpression(target) && WRITING_HOOKS.some((hook) => isCallTo(unwrap(target), [hook]))) return trusted(mode)
      const hook = rowMenuHookOf(target, scope)
      if (hook === 'ambiguous') return UNRESOLVED
      const object = hook?.returned ?? objectLiteralOf(target)
      if (!object) return UNRESOLVED
      return membersWays(access, object, memberJudge(hook?.scope ?? scope, mode))
    }),
  )
}

/** Judges a member of an object literal (a local's, or a row-menu hook's) as the function it is called as. */
function memberJudge(where: FileScope, mode: Mode): (member: ts.ObjectLiteralElementLike) => Ways {
  return (member) => {
    if (ts.isPropertyAssignment(member)) return localValueCallWays(member.initializer, where, mode)
    if (ts.isShorthandPropertyAssignment(member)) return nameCallWays(member.name.text, where, mode)
    if (ts.isMethodDeclaration(member)) return functionWays(member, where, mode)
    return UNRESOLVED
  }
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
      // A parameter or a destructured name of the same spelling elsewhere in
      // the file may be what this value means (KAN-518): neither can be followed.
      const shadowed = scope.params.has(expr.text) || scope.destructured.has(expr.text) ? [UNRESOLVED] : []
      return either(
        ...shadowed,
        ...targets.map((t) => (ts.isFunctionDeclaration(t) ? functionWays(t, scope, 'body') : handlerWays(t as ts.Expression, scope))),
      )
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
    const scope = fileScope(file, source.path, rowMenuHooks, state)
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
    // KAN-518: the plain hook sits beside a row-menu hook, so the file is read and the name check decides.
    const plainHook: Source = {
      path: 'components/useThingRowMenu.tsx',
      text: "export function useThingRowMenu() { return { go: () => setActiveTab('identity') } }\nexport function useThing() { return { go: () => setAllocating(true) } }",
    }
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

  // KAN-518: the shapes KAN-512's reviews found passing unjudged — each cut to
  // a tab switch goes red, and what does real work (a state setter, navigate)
  // still passes.
  it('follows destructured names, hook results, callbacks handed to an import and reassigned lets', () => {
    const builders = primaryHandlersOfBuilders([
      {
        path: 'components/oldRowMenu.tsx',
        text: `export function buildOldRowMenu(t, handlers) {
          return { primary: { label: t('allocate'), onClick: handlers.onAllocate }, overflow: {} }
        }`,
      },
    ])
    const verdictOf = (sources: Source[]): string => {
      const found = noOpPrimaries(sources, builders)
      if (found.length === 0) return 'ok'
      return found[0].endsWith('(cannot be followed)') ? 'cannot be followed' : 'tab switch'
    }
    /** The verdict for one page whose primary is `handler`, given `setup` before it (and `head` above the component). */
    const verdict = (setup: string, handler: string, { head = '', signature = 'A()', more = [] as Source[] } = {}): string =>
      verdictOf([
        ...more,
        {
          path: 'pages/P.tsx',
          text: `${head}
          export function ${signature} {
            ${setup}
            const rowMenu = buildOldRowMenu(t, { onAllocate: ${handler} })
            return <DetailHeader primaryAction={rowMenu.primary} />
          }`,
        },
      ])

    // A name destructured from a call's result cannot be followed …
    expect.soft(verdict('const { allocate } = useStartActions()', '() => allocate()')).toBe('cannot be followed')
    expect.soft(verdict('const [, start] = useDispatcher()', "() => start('allocate')")).toBe('cannot be followed')
    expect.soft(verdict('const { actions: { allocate } } = useStartActions()', '() => allocate()')).toBe('cannot be followed')
    // … nor one destructured from a prop, nor a call through a destructured receiver.
    expect.soft(verdict('const { onAllocateProp } = props', '() => onAllocateProp()', { signature: 'A(props)' })).toBe('cannot be followed')
    expect.soft(verdict('const { menu } = props', '() => menu.go()', { signature: 'A(props)' })).toBe('cannot be followed')
    // A local holding a hook's result: KAN-503's dispatcher moved into a hook.
    expect.soft(verdict('const startAction = useStartAction()', "() => startAction('allocate')")).toBe('cannot be followed')
    // A callback handed to an import or a global runs: judged as if it did.
    expect.soft(verdict('', "() => startTransition(() => setActiveTab('identity'))")).toBe('tab switch')
    expect.soft(verdict('', "() => { setTimeout(() => setActiveTab('identity'), 0) }")).toBe('tab switch')
    expect.soft(verdict('', "() => React.startTransition(function () { setActiveTab('identity') })")).toBe('tab switch')
    expect.soft(verdict('', "() => startTransition(() => { setActiveTab('identity'); setAllocating(true) })")).toBe('ok')
    // A `let` handler is judged by every value it can hold.
    expect.soft(verdict("let go = () => setAllocating(true)\nif (legacy) go = () => setActiveTab('identity')", 'go')).toBe('tab switch')
    expect.soft(verdict("let go = () => setAllocating(true)\ngo ??= () => setActiveTab('identity')", '() => go()')).toBe('tab switch')
    expect.soft(verdict("let go = () => setAllocating(true)\nif (legacy) go = () => setFormToFocus('x')", 'go')).toBe('ok')

    // What the scan can read is followed: a member of a local object, its default, a row-menu hook's member.
    const actions = "const actions = { allocate: () => setActiveTab('identity'), work: () => setAllocating(true) }"
    expect.soft(verdict(`${actions}; const { allocate } = actions`, '() => allocate()')).toBe('tab switch')
    expect.soft(verdict(`${actions}; const { work: go } = actions`, '() => go()')).toBe('ok')
    expect.soft(verdict(`${actions}; const { work = () => setActiveTab('identity') } = actions`, '() => work()')).toBe('tab switch')
    expect.soft(verdict(`${actions}; const { ...rest } = actions`, '() => rest()')).toBe('cannot be followed')
    const hookFile = (go: string): Source => ({ path: 'components/useThingRowMenu.tsx', text: `export function useThingRowMenu() { return { go: ${go} } }` })
    const importsHook = "import { useThingRowMenu } from '../components/useThingRowMenu'"
    expect.soft(verdict('const { go } = useThingRowMenu()', '() => go()', { head: importsHook, more: [hookFile('() => setAllocating(true)')] })).toBe('ok')
    expect.soft(verdict('const { go } = useThingRowMenu()', '() => go()', { head: importsHook, more: [hookFile("() => setActiveTab('identity')")] })).toBe(
      'tab switch',
    )

    // The values named with a reason pass where they do work, and a tab switch beside them still counts.
    expect.soft(verdict('const [allocating, setAllocating] = useState(false)', "() => { setActiveTab('identity'); setAllocating(true) }")).toBe('ok')
    expect.soft(verdict('const [open, setOpen] = useState(false)', "() => { setActiveTab('identity'); setOpen((o) => !o) }")).toBe('ok')
    expect.soft(verdict('const [state, dispatch] = useReducer(reduce, initial)', "() => { setActiveTab('identity'); dispatch({ type: 'allocate' }) }")).toBe('ok')
    expect.soft(verdict('const navigate = useNavigate()', "() => { setActiveTab('identity'); navigate('/x') }")).toBe('ok')
    expect.soft(verdict('const [params, setSearchParams] = useSearchParams()', "() => setSearchParams({ q: 'allocate' })")).toBe('ok')
    expect.soft(verdict('const [params, setSearchParams] = useSearchParams()', "() => setSearchParams({ tab: 'identity' })")).toBe('tab switch')
    expect.soft(verdict('const { t } = useTranslation()', "() => { t('allocate'); setActiveTab('identity') }")).toBe('tab switch')
    expect.soft(verdict('const { t } = useTranslation()', "() => notifications.show({ message: t('allocate') })")).toBe('ok')
    // … but only that value of that hook.
    expect.soft(verdict('const [allocating, setAllocating] = useState(false)', '() => allocating()')).toBe('cannot be followed')
    expect.soft(verdict('const { i18n } = useTranslation()', "() => i18n.changeLanguage('fr')")).toBe('cannot be followed')

    // KAN-518 hygiene: a row-menu hook is the module the page imports, never a namesake.
    const working = hookFile('() => setAllocating(true)')
    const tabbing = hookFile("() => setActiveTab('identity')")
    expect.soft(verdict('const menu = useThingRowMenu()', '() => menu.go()', { head: importsHook, more: [working] })).toBe('ok')
    expect.soft(verdict('const menu = useThingRowMenu()', '() => menu.go()', { head: "import { useThingRowMenu } from '@vendor/menus'", more: [working] })).toBe(
      'cannot be followed',
    )
    expect.soft(verdict('const menu = useThingRowMenu()', '() => menu.go()', { head: "import { useThingRowMenu } from './useThingRowMenu'", more: [working] })).toBe(
      'cannot be followed',
    )
    expect.soft(
      verdict('const useThingRowMenu = () => ({ go: () => setAllocating(true) }); const menu = useThingRowMenu()', '() => menu.go()', {
        head: importsHook,
        more: [tabbing],
      }),
    ).toBe('cannot be followed')
    expect.soft(
      verdict('const menu = useMenu()', '() => menu.go()', { head: "import { useThingRowMenu as useMenu } from '../components/useThingRowMenu'", more: [tabbing] }),
    ).toBe('tab switch')
    // An ambiguous hook reached as a handler value (not called) cannot be followed either.
    const otherTabbing: Source = { ...tabbing, path: 'components/other/useThingRowMenu.tsx' }
    expect.soft(verdict('const menu = useThingRowMenu()', 'menu.go', { head: importsHook, more: [working, otherTabbing] })).toBe('cannot be followed')
    expect.soft(verdict('const menu = useThingRowMenu()', 'menu.go', { head: importsHook, more: [working] })).toBe('ok')

    // KAN-518 review. Names are judged file-wide, the strictest meaning decides: a prop of the
    // same name in another component is a meaning that cannot be followed.
    const twoComponents = (child: string, setup: string, handler: string): Source => ({
      path: 'pages/P.tsx',
      text: `export function Child(${child}) { return null }
      export function A() {
        ${setup}
        const rowMenu = buildOldRowMenu(t, { onAllocate: ${handler} })
        return <DetailHeader primaryAction={rowMenu.primary} />
      }`,
    })
    expect.soft(verdictOf([twoComponents('{ go }', 'const [x, go] = useState(false)', '() => go()')])).toBe('cannot be followed')
    expect.soft(verdictOf([twoComponents('{ go }', 'const go = () => setAllocating(true)', '() => go()')])).toBe('cannot be followed')
    expect.soft(verdictOf([twoComponents('{ go }', 'const go = () => setAllocating(true)', 'go')])).toBe('cannot be followed')
    expect.soft(verdictOf([twoComponents('{ other }', 'const go = () => setAllocating(true)', 'go')])).toBe('ok')
    // The URL setter under another name switches a tab like setSearchParams.
    expect.soft(verdict('const [params, setParams] = useSearchParams()', "() => setParams({ tab: 'identity' })")).toBe('tab switch')
    expect.soft(verdict('const [params, setParams] = useSearchParams()', "() => setParams({ q: 'allocate' })")).toBe('ok')
    // A destructured name met again while it is judged cannot be followed — never a stack overflow.
    const loop: Source = {
      path: 'pages/P.tsx',
      text: `export function B() { const { allocate } = actions; return null }
      export function A() {
        const allocate = () => setAllocating(true)
        const actions = { allocate }
        const rowMenu = buildOldRowMenu(t, { onAllocate: () => allocate() })
        return <DetailHeader primaryAction={rowMenu.primary} />
      }`,
    }
    expect.soft(() => verdictOf([loop])).not.toThrow()
    expect.soft(verdictOf([loop])).toBe('cannot be followed')
    // A destructured `let` reassigned; a hook through a namespace, `React.useCallback`, `use(Context)`;
    // a named function handed to an import.
    expect.soft(verdict("let [x, go] = useState(false)\nif (legacy) go = () => setActiveTab('identity')", '() => go()')).toBe('tab switch')
    expect.soft(verdict('const startAction = Hooks.useStartAction()', "() => startAction('allocate')")).toBe('cannot be followed')
    expect.soft(verdict("const go = React.useCallback(() => setActiveTab('identity'), [])", '() => go()')).toBe('tab switch')
    expect.soft(verdict('const run = use(ActionsContext)', '() => run()')).toBe('cannot be followed')
    expect.soft(verdict("const goOverview = () => setActiveTab('identity')", '() => startTransition(goOverview)')).toBe('tab switch')
    expect.soft(verdict("const label = 'x'", '() => notifications.show(label)')).toBe('ok')
    // The trusted values through a namespace, a mutation's mutate destructured, an own-property lookup only.
    expect.soft(verdict('const [x, setAllocating] = React.useState(false)', "() => { setActiveTab('identity'); setAllocating(true) }")).toBe('ok')
    expect.soft(verdict('const { mutate } = useMutation({ mutationFn })', "() => { setActiveTab('identity'); mutate() }")).toBe('ok')
    expect.soft(() => verdict('const { go } = constructor()', '() => go()')).not.toThrow()
    expect.soft(verdict('const { go } = constructor()', '() => go()')).toBe('cannot be followed')
    // … and only from their own package: a namesake imported from elsewhere is not trusted.
    const setterThenTab = "() => { setActiveTab('identity'); setAllocating(true) }"
    expect.soft(verdict('const [x, setAllocating] = useState(false)', setterThenTab, { head: "import { useState } from 'react'" })).toBe('ok')
    expect.soft(verdict('const [x, setAllocating] = React.useState(false)', setterThenTab, { head: "import * as React from 'react'" })).toBe('ok')
    expect.soft(verdict('const [x, setAllocating] = useState(false)', setterThenTab, { head: "import { useState } from '../hooks/useState'" })).toBe(
      'cannot be followed',
    )
    expect.soft(verdict('const navigate = useNavigate()', "() => navigate('/x')", { head: "import { useNavigate } from 'react-router-dom'" })).toBe('ok')
    expect.soft(verdict('const navigate = useNavigate()', "() => navigate('/x')", { head: "import { useNavigate } from '../hooks/useNavigate'" })).toBe(
      'cannot be followed',
    )

    // A `finally` that leaves decides how the function ends (`c.done || w.done`): its return
    // overrides the try's throw, its throw overrides the try's return.
    expect.soft(verdict("const f = () => { try { setActiveTab('identity'); throw 'x' } finally { return } }", '() => { f(); setAllocating(true) }')).toBe('ok')
    expect.soft(verdict("const f = () => { try { setActiveTab('identity'); return } finally { throw 'x' } }", '() => { f(); setAllocating(true) }')).toBe(
      'tab switch',
    )
  })
})
