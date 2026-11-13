/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// KAN-526 — Mantine's useTransition runs two animation frames and a timer
// per transition unless reduced motion is respected; one such timer fired
// after its file's jsdom was gone and failed a fully green run. Every render
// test therefore mounts Mantine with `testTheme` (src/test/testTheme.ts,
// `respectReducedMotion`), through renderWithProviders or its own provider.
//
// KAN-543 — the guard reads each file with the TypeScript compiler, as
// grid-default-column-layout.test.ts does, so comments and strings are never
// code and an import is seen for what it binds. In a test (`*.test.*`,
// `*.spec.*`) or in src/test, every use of Mantine's provider must be a
// `<MantineProvider …>` tag — under whatever local name it was imported as —
// whose `theme` is the `testTheme` imported from src/test/testTheme.ts. Any
// other use (`wrapper: MantineProvider`, `createElement(MantineProvider, …)`,
// a provider handed to a helper) cannot be judged and is refused, and so is a
// `testTheme` declared locally, imported from another file, or another
// export aliased into that name. The ui-kit has the same guard
// (packages/ui-kit/src/test/renderTestsMountMantineWithTestTheme.test.ts);
// a change to one is made to both.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SHARED_TEST_THEME = 'src/test/testTheme'

function files(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) files(full, out)
    else if (/\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

/**
 * A test (`*.test.*`, `*.spec.*`) or a test helper — anything under src/test or under a
 * module's own test/ folder (ADR-161, KAN-636) — by its src-relative path. The ui-kit's twin
 * has no module folders, so this clause is the app's alone.
 */
export function isScanned(path: string): boolean {
  return /\.(test|spec)\.tsx?$/.test(path) || path.startsWith('test/') || /^modules\/[^/]+\/test\//.test(path)
}

/** `a/b/../c/./d` → `a/c/d`, on `/`-separated paths. */
function normalise(path: string): string {
  const out: string[] = []
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return out.join('/')
}

function resolvesTo(fileName: string, specifier: string, target: string): boolean {
  if (!specifier.startsWith('.')) return false
  const dir = fileName.split('/').slice(0, -1).join('/')
  return normalise(`${dir}/${specifier}`).replace(/\.tsx?$/, '') === target
}

/** Every Mantine provider `text` (the file `fileName`, a `src/`-relative
 * path) mounts without the shared test theme, as `<line>: <code>`. */
export function providersWithoutTestTheme(text: string, fileName = 'src/pages/probe.test.tsx'): string[] {
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const providerNames = new Set(['MantineProvider'])
  const mantineNamespaces = new Set<string>()
  const sharedThemeNames = new Set<string>()
  for (const statement of file.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue
    const specifier = statement.moduleSpecifier.text
    const bindings = statement.importClause?.namedBindings
    if (bindings && ts.isNamespaceImport(bindings) && specifier === '@mantine/core') mantineNamespaces.add(bindings.name.text)
    if (!bindings || !ts.isNamedImports(bindings)) continue
    for (const element of bindings.elements) {
      const imported = (element.propertyName ?? element.name).text
      if (specifier === '@mantine/core' && imported === 'MantineProvider') providerNames.add(element.name.text)
      if (imported === 'testTheme' && resolvesTo(fileName, specifier, SHARED_TEST_THEME)) sharedThemeNames.add(element.name.text)
    }
  }

  const offences: string[] = []
  const offence = (node: ts.Node, what: string) => {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1
    offences.push(`${line}: ${what}`)
  }
  const isProvider = (node: ts.Node): boolean =>
    (ts.isIdentifier(node) && providerNames.has(node.text)) ||
    (ts.isPropertyAccessExpression(node) &&
      ts.isIdentifier(node.expression) &&
      mantineNamespaces.has(node.expression.text) &&
      node.name.text === 'MantineProvider')
  // A spread can carry (or override) any theme, so a tag with one cannot be judged.
  const passesSharedTheme = (attributes: ts.JsxAttributes): boolean =>
    !attributes.properties.some((attribute) => ts.isJsxSpreadAttribute(attribute)) &&
    attributes.properties.some(
      (attribute) =>
        ts.isJsxAttribute(attribute) &&
        attribute.name.getText(file) === 'theme' &&
        attribute.initializer !== undefined &&
        ts.isJsxExpression(attribute.initializer) &&
        attribute.initializer.expression !== undefined &&
        ts.isIdentifier(attribute.initializer.expression) &&
        sharedThemeNames.has(attribute.initializer.expression.text),
    )

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) || ts.isJsxClosingElement(node)) return
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && isProvider(node.tagName)) {
      if (!passesSharedTheme(node.attributes)) offence(node, node.getText(file).split('\n')[0])
      ts.forEachChild(node.attributes, visit)
      return
    }
    if (isProvider(node)) {
      offence(node, node.parent.getText(file).split('\n')[0])
      return
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return offences
}

describe('render tests mount Mantine with the test theme (KAN-526, KAN-543)', () => {
  it('every MantineProvider a test mounts passes testTheme', () => {
    const scanned = files(SRC_ROOT).filter((f) => isScanned(relative(SRC_ROOT, f).split('\\').join('/')))
    const offences = scanned.flatMap((f) => {
      const path = `src/${relative(SRC_ROOT, f).split('\\').join('/')}`
      return providersWithoutTestTheme(readFileSync(f, 'utf8'), path).map((o) => `${path}:${o}`)
    })
    expect(scanned.length).toBeGreaterThan(100)
    // renderWithProviders mounts the provider every render test goes through.
    expect(scanned.some((f) => f.endsWith(join('test', 'renderWithProviders.tsx')))).toBe(true)
    expect(offences).toEqual([])
  })

  const imports = "import { Button, MantineProvider } from '@mantine/core'\nimport { testTheme } from '../test/testTheme'\n"

  it('passes the shared theme, under any local name for either import', () => {
    expect(providersWithoutTestTheme(`${imports}render(<MantineProvider env="test" theme={testTheme} withCssVariables={false}><Button /></MantineProvider>)`)).toEqual([])
    expect(providersWithoutTestTheme(`${imports}render(<MantineProvider theme={testTheme} />)`)).toEqual([])
    expect(
      providersWithoutTestTheme(
        "import { MantineProvider as MP } from '@mantine/core'\nimport { testTheme as shared } from '../test/testTheme'\n<MP theme={shared}>x</MP>",
      ),
    ).toEqual([])
    expect(providersWithoutTestTheme("import { MantineProvider } from '@mantine/core'\nimport { testTheme } from './testTheme'\n<MantineProvider theme={testTheme} />", 'src/test/renderWithProviders.tsx')).toEqual([])
    // A comment or a string naming it is not code.
    expect(providersWithoutTestTheme(`${imports}// wrapper: MantineProvider\nit('mounts a MantineProvider', () => {})`)).toEqual([])
  })

  it('scans tests, specs, src/test and a module\'s test/ folder, nothing else', () => {
    expect(
      [
        'pages/X.test.tsx',
        'pages/X.spec.tsx',
        'utils/y.spec.ts',
        'test/renderWithProviders.tsx',
        'modules/valuation/test/nameValuationCar.ts',
      ].every(isScanned),
    ).toBe(true)
    expect(['pages/X.tsx', 'utils/specials.ts', 'modules/valuation/pages/X.tsx', 'modules/test/X.tsx'].some(isScanned)).toBe(false)
  })

  it('goes red for a tag with another theme or none', () => {
    expect(providersWithoutTestTheme(`${imports}<MantineProvider theme={theme}>x</MantineProvider>`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}<MantineProvider>x</MantineProvider>`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}<MantineProvider {...props} />`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}<MantineProvider theme={testTheme} {...rest}>x</MantineProvider>`)).toHaveLength(1)
  })

  it('goes red for a provider mounted any other way', () => {
    expect(providersWithoutTestTheme(`${imports}renderHook(() => useX(), { wrapper: MantineProvider })`)).toEqual([
      '3: wrapper: MantineProvider',
    ])
    expect(providersWithoutTestTheme(`${imports}render(createElement(MantineProvider, { theme }, ui))`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}mountWith(MantineProvider)`)).toHaveLength(1)
    expect(providersWithoutTestTheme("import * as M from '@mantine/core'\nrenderHook(f, { wrapper: M.MantineProvider })")).toHaveLength(1)
  })

  it('goes red for the three shapes text matching could not judge', () => {
    // An aliased import, then the alias as the tag.
    expect(providersWithoutTestTheme("import { MantineProvider as MP } from '@mantine/core'\n<MP theme={theme}>x</MP>")).toEqual([
      '2: <MP theme={theme}>',
    ])
    // A `/*` inside a string hides nothing.
    expect(providersWithoutTestTheme(`${imports}const glob = '/*'\nrenderHook(f, { wrapper: MantineProvider })\nconst end = '*/'`)).toHaveLength(1)
    // A testTheme that is not the shared one: declared here, imported from
    // elsewhere, or another export aliased into that name.
    const tag = '<MantineProvider theme={testTheme} />'
    expect(providersWithoutTestTheme(`import { MantineProvider, createTheme } from '@mantine/core'\nconst testTheme = createTheme({})\n${tag}`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`import { MantineProvider } from '@mantine/core'\nimport { testTheme } from './myTheme'\n${tag}`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`import { MantineProvider } from '@mantine/core'\nimport { theme as testTheme } from '../test/testTheme'\n${tag}`)).toHaveLength(1)
  })
})
