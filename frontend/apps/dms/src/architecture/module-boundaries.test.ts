/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, posix, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// KAN-636 (ADR-161): the frontend follows the modules a user knows. A module's
// pages, components, hooks, options and tests live in src/modules/<module>/,
// and its index.ts is its one door. This guard holds three rules:
//
// 1. Production code outside a module imports it only through its index.ts.
//    Two kinds of file may reach in: the router (App.tsx), which lazy-loads a
//    module's pages by path so each route stays its own chunk (KAN-233), and
//    test code (a test, a spec, a test helper), which renders what it tests.
//    That holds between modules too.
// 2. Every module folder has an index.ts and an entry in MODULE_WORDS — the
//    words its file names carry — and every entry names a module folder.
// 3. Once a module has moved, none of its files stays behind: no file outside
//    src/modules/ (src/api and src/i18n are shared, src/architecture holds the
//    guards) carries one of the module's words in its name, and no path a move
//    mapping in scripts/module-moves/ moved away from exists again.
// 4. A module's index.ts never re-exports (or imports) from its own pages/.
//    Rollup keeps a re-exported module even when its exports go unused, so a
//    door that names a page pulls the page into every chunk that imports the
//    door — the eager shell's included. A content component another module
//    renders lives in the module's components/; what the shell or several
//    modules use lives in src/shared/ (ADR-161 amendment, KAN-636 step 2a).
// 5. A door holds re-exports (`export … from '…'`) and nothing else. The build
//    (vite.config.ts) declares every door free of side effects so a screen
//    that imports one of a module's exports loads that file only, not every
//    file the door names (KAN-636 step 2b); a door with code of its own would
//    make that declaration false.
//
// Modules move one per PR (scripts/move-to-module.mjs). Until the last one, a
// module may still import the old roots (pages/, components/, hooks/, utils/,
// …); each later move points those imports at src/shared/ or at another
// module's index.ts.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const APP_ROOT = join(SRC_ROOT, '..')
const MOVES_DIR = join(APP_ROOT, 'scripts', 'module-moves')

/** Each module folder → the words its file names carry (camelCase, capitalised). */
const MODULE_WORDS: Record<string, string[]> = {
  customer: ['Customer'],
  valuation: ['Valuation'],
}

/** Outside src/modules/, these folders may carry a module's word: shared API types, bundles, the guards. */
const NAME_CHECK_EXEMPT_DIRS = ['api/', 'i18n/', 'architecture/']

const RESOLVE_SUFFIXES = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']

type Import = { specifier: string; dynamic: boolean }

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) sourceFiles(full, out)
    else if (/\.tsx?$/.test(entry) && !entry.endsWith('.d.ts')) out.push(full)
  }
  return out
}

const srcRelative = (full: string) => relative(SRC_ROOT, full).split('\\').join('/')

/** Every module specifier a file names: imports, re-exports and `import()` calls. */
export function importsOf(path: string, source: string): Import[] {
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, false, kind)
  const found: Import[] = []
  const visit = (node: ts.Node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      found.push({ specifier: node.moduleSpecifier.text, dynamic: false })
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length > 0 &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      found.push({ specifier: node.arguments[0].text, dynamic: true })
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

/** The src-relative file a relative specifier names, or null (a package, or nothing there). */
export function resolveImport(importer: string, specifier: string, exists: (path: string) => boolean): string | null {
  if (!specifier.startsWith('.')) return null
  const base = posix.normalize(posix.join(posix.dirname(importer), specifier))
  for (const suffix of RESOLVE_SUFFIXES) {
    if (exists(base + suffix)) return base + suffix
  }
  return null
}

const moduleOf = (path: string): string | null => /^modules\/([^/]+)\//.exec(path)?.[1] ?? null

/** A test, a spec, or a test helper (src/test/, a module's test/). */
export function isTestCode(path: string): boolean {
  return /\.(test|spec)\.tsx?$/.test(path) || path.startsWith('test/') || /^modules\/[^/]+\/test\//.test(path)
}

/** Rule 1: every import that reaches into a module from outside it other than through index.ts. */
export function boundaryViolations(files: Map<string, string>): string[] {
  const exists = (path: string) => files.has(path)
  const out: string[] = []
  for (const [importer, source] of files) {
    if (isTestCode(importer)) continue
    const own = moduleOf(importer)
    for (const { specifier, dynamic } of importsOf(importer, source)) {
      const target = resolveImport(importer, specifier, exists)
      const module = target ? moduleOf(target) : null
      if (!target || !module || module === own) continue
      if (target === `modules/${module}/index.ts` || target === `modules/${module}/index.tsx`) continue
      if (importer === 'App.tsx' && dynamic && target.startsWith(`modules/${module}/pages/`)) continue
      out.push(`${importer}: '${specifier}' reaches into modules/${module}/ past its index.ts`)
    }
  }
  return out.sort()
}

/** Whether a file name carries `word` as a camelCase word: `NewValuationButton`, `valuationOptions` — not `EvaluationTab`. */
export function carriesWord(fileName: string, word: string): boolean {
  const lower = word[0].toLowerCase() + word.slice(1)
  return fileName
    .split('.')
    .slice(0, -1)
    .some((segment) => segment.includes(word) || segment.startsWith(lower))
}

/** Rule 3, by name: a file outside src/modules/ carrying a moved module's word. */
export function leftBehind(paths: string[], moduleWords: Record<string, string[]>): string[] {
  return paths
    .filter((path) => !path.startsWith('modules/') && !NAME_CHECK_EXEMPT_DIRS.some((dir) => path.startsWith(dir)))
    .flatMap((path) => {
      const name = path.split('/').pop()!
      return Object.entries(moduleWords)
        .filter(([, words]) => words.some((word) => carriesWord(name, word)))
        .map(([module]) => `${path} belongs to modules/${module}/`)
    })
    .sort()
}

/** Rule 4: every specifier a module's index.ts names that resolves into that module's pages/. */
export function doorReachesPages(files: Map<string, string>): string[] {
  const exists = (path: string) => files.has(path)
  const out: string[] = []
  for (const [path, source] of files) {
    const door = /^modules\/([^/]+)\/index\.tsx?$/.exec(path)
    if (!door) continue
    for (const { specifier } of importsOf(path, source)) {
      const target = resolveImport(path, specifier, exists)
      if (target?.startsWith(`modules/${door[1]}/pages/`)) out.push(`${path}: '${specifier}' names a page; a door exports components, hooks and options only`)
    }
  }
  return out.sort()
}

/** Rule 5: every statement of a module's index.ts that is not a re-export. */
export function doorHoldsMoreThanReExports(files: Map<string, string>): string[] {
  const out: string[] = []
  for (const [path, source] of files) {
    if (!/^modules\/[^/]+\/index\.tsx?$/.test(path)) continue
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, false, ts.ScriptKind.TS)
    for (const statement of file.statements) {
      if (ts.isExportDeclaration(statement) && statement.moduleSpecifier) continue
      const line = file.getLineAndCharacterOfPosition(statement.getStart(file)).line + 1
      out.push(`${path}:${line} is not a re-export; a door holds \`export … from '…'\` only`)
    }
  }
  return out.sort()
}

type MoveMapping = { module: string; moves: [string, string][] }

function moveMappings(): MoveMapping[] {
  return readdirSync(MOVES_DIR)
    .filter((name) => name.endsWith('.json'))
    .map((name) => JSON.parse(readFileSync(join(MOVES_DIR, name), 'utf-8')) as MoveMapping)
}

const realFiles = new Map(sourceFiles(SRC_ROOT).map((full) => [srcRelative(full), readFileSync(full, 'utf-8')]))
const moduleFolders = readdirSync(join(SRC_ROOT, 'modules')).filter((name) => statSync(join(SRC_ROOT, 'modules', name)).isDirectory())

describe('frontend modules keep their boundary (KAN-636, ADR-161)', () => {
  it('production code reaches a module only through its index.ts, the router\'s lazy pages aside', () => {
    expect(boundaryViolations(realFiles)).toEqual([])
    // Not vacuous: another module's screens do use valuation, through its door.
    const throughTheDoor = [...realFiles].filter(
      ([path, source]) => moduleOf(path) !== 'valuation' && !isTestCode(path) && importsOf(path, source).some(({ specifier }) => /(^|\/)modules\/valuation$/.test(specifier)),
    )
    expect(throughTheDoor.map(([path]) => path).sort()).toEqual([
      'components/deal-workspace/DealWorkspaceContainers.tsx',
      'components/stock-detail/EvaluationTab.tsx',
      'pages/ContractDetailPage.tsx',
    ])
    // And customer's: the Vehicle 360, the deal workspace, the contract page, the offer workspace and valuation.
    const throughCustomersDoor = [...realFiles].filter(
      ([path, source]) => moduleOf(path) !== 'customer' && !isTestCode(path) && importsOf(path, source).some(({ specifier }) => /(^|\/)(modules\/)?customer$/.test(specifier) && resolveImport(path, specifier, (p) => realFiles.has(p)) === 'modules/customer/index.ts'),
    )
    expect(throughCustomersDoor.map(([path]) => path).sort()).toEqual([
      'components/deal-workspace/DealWorkspaceContainers.tsx',
      'components/vehicle-detail/IdentityTab.tsx',
      'modules/valuation/components/ValuationCreateDialog.tsx',
      'modules/valuation/pages/ValuationDetailPage.tsx',
      'pages/ContractDetailPage.tsx',
      'pages/OfferWorkspacePage.tsx',
    ])
  })

  it('every module folder has an index.ts and its words, and every entry names a folder', () => {
    expect(moduleFolders.length).toBeGreaterThan(0)
    expect(moduleFolders.filter((module) => !existsSync(join(SRC_ROOT, 'modules', module, 'index.ts')))).toEqual([])
    expect([...moduleFolders].sort()).toEqual(Object.keys(MODULE_WORDS).sort())
  })

  it("a module's index.ts names none of its pages", () => {
    expect([...realFiles.keys()].filter((path) => /^modules\/[^/]+\/index\.ts$/.test(path)).length).toBeGreaterThan(0)
    expect(doorReachesPages(realFiles)).toEqual([])
  })

  it("a module's index.ts holds re-exports and nothing else", () => {
    expect(doorHoldsMoreThanReExports(realFiles)).toEqual([])
  })

  it('no file of a moved module stays in the old roots', () => {
    expect(leftBehind([...realFiles.keys()], MODULE_WORDS)).toEqual([])
    const mappings = moveMappings()
    expect(mappings.map((m) => m.module).sort()).toEqual([...moduleFolders].sort())
    const backAgain = mappings.flatMap((m) => m.moves.filter(([from]) => existsSync(join(APP_ROOT, from))).map(([from]) => from))
    const notThere = mappings.flatMap((m) => m.moves.filter(([, to]) => !existsSync(join(APP_ROOT, to))).map(([, to]) => to))
    expect(backAgain).toEqual([])
    expect(notThere).toEqual([])
  })

  describe('the rules bite', () => {
    const tree = (entries: Record<string, string>) => new Map(Object.entries(entries))
    const door = "export { ValuationSourceMarker } from './components/ValuationSourceMarker'\n"
    const valuation = {
      'modules/valuation/index.ts': door,
      'modules/valuation/components/ValuationSourceMarker.tsx': 'export function ValuationSourceMarker() { return null }\n',
      'modules/valuation/pages/ValuationsListPage.tsx': "import { ValuationSourceMarker } from '../components/ValuationSourceMarker'\n",
    }

    it('refuses a production import past index.ts, from the old roots or another module', () => {
      expect(
        boundaryViolations(
          tree({
            ...valuation,
            'components/deal-workspace/X.tsx': "import { ValuationSourceMarker } from '../../modules/valuation/components/ValuationSourceMarker'\n",
            'modules/stock/components/Y.tsx': "export { ValuationSourceMarker } from '../../valuation/components/ValuationSourceMarker'\n",
            'App.tsx': "import { ValuationsListPage } from './modules/valuation/pages/ValuationsListPage'\nconst D = lazyPage(() => import('./modules/valuation/components/ValuationSourceMarker'), 'D')\n",
          }),
        ),
      ).toEqual([
        "App.tsx: './modules/valuation/components/ValuationSourceMarker' reaches into modules/valuation/ past its index.ts",
        "App.tsx: './modules/valuation/pages/ValuationsListPage' reaches into modules/valuation/ past its index.ts",
        "components/deal-workspace/X.tsx: '../../modules/valuation/components/ValuationSourceMarker' reaches into modules/valuation/ past its index.ts",
        "modules/stock/components/Y.tsx: '../../valuation/components/ValuationSourceMarker' reaches into modules/valuation/ past its index.ts",
      ])
    })

    it('lets through the door, the router\'s lazy pages, a module\'s own files and test code', () => {
      expect(
        boundaryViolations(
          tree({
            ...valuation,
            'components/deal-workspace/X.tsx': "import { ValuationSourceMarker } from '../../modules/valuation'\n",
            'App.tsx': "const L = lazyPage(() => import('./modules/valuation/pages/ValuationsListPage'), 'ValuationsListPage')\n",
            'pages/statusBadges.i18n.render.test.tsx': "import { ValuationsListPage } from '../modules/valuation/pages/ValuationsListPage'\n",
            'test/helper.ts': "import { ValuationSourceMarker } from '../modules/valuation/components/ValuationSourceMarker'\n",
            'modules/stock/test/helper.ts': "import { ValuationSourceMarker } from '../../valuation/components/ValuationSourceMarker'\n",
          }),
        ),
      ).toEqual([])
    })

    it("refuses a door that re-exports or imports a page of its own module, and only that", () => {
      expect(
        doorReachesPages(
          tree({
            ...valuation,
            'modules/valuation/index.ts': `${door}export { ValuationsListPage } from './pages/ValuationsListPage'\nimport './pages/ValuationsListPage.tsx'\n`,
            'modules/stock/index.ts': "export { ValuationSourceMarker } from '../valuation'\n",
            'modules/stock/pages/StockPage.tsx': 'export function StockPage() { return null }\n',
            'modules/stock/components/StockCard.tsx': "export { StockPage } from '../pages/StockPage'\n",
          }),
        ),
      ).toEqual([
        "modules/valuation/index.ts: './pages/ValuationsListPage' names a page; a door exports components, hooks and options only",
        "modules/valuation/index.ts: './pages/ValuationsListPage.tsx' names a page; a door exports components, hooks and options only",
      ])
      expect(doorReachesPages(tree(valuation))).toEqual([])
    })

    it('refuses a door with code of its own, a plain import included', () => {
      expect(
        doorHoldsMoreThanReExports(
          tree({
            ...valuation,
            'modules/valuation/index.ts': `${door}import './components/ValuationSourceMarker'\nexport const x = 1\nexport type { Y } from './components/Y'\n`,
          }),
        ),
      ).toEqual([
        "modules/valuation/index.ts:2 is not a re-export; a door holds `export … from '…'` only",
        "modules/valuation/index.ts:3 is not a re-export; a door holds `export … from '…'` only",
      ])
      expect(doorHoldsMoreThanReExports(tree(valuation))).toEqual([])
    })

    it('a comment or a string naming a path is not an import', () => {
      expect(importsOf('X.tsx', "// import { A } from './modules/valuation/components/A'\nconst s = \"from './modules/valuation/pages/B'\"\n")).toEqual([])
    })

    it('finds a file left behind by its name, as a camelCase word only', () => {
      expect(
        leftBehind(
          [
            'pages/ValuationFooPage.tsx',
            'components/valuationThing.ts',
            'components/NewValuationButton.tsx',
            'pages/statusBadges.valuation.render.test.tsx',
            'components/stock-detail/EvaluationTab.tsx',
            'api/types.ts',
            'i18n/locales/de/valuationCreate.json',
            'modules/valuation/pages/ValuationsListPage.tsx',
          ],
          { valuation: ['Valuation'] },
        ),
      ).toEqual([
        'components/NewValuationButton.tsx belongs to modules/valuation/',
        'components/valuationThing.ts belongs to modules/valuation/',
        'pages/ValuationFooPage.tsx belongs to modules/valuation/',
        'pages/statusBadges.valuation.render.test.tsx belongs to modules/valuation/',
      ])
    })
  })
})
