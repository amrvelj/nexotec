import { describe, expect, it } from 'vitest'
import ts from 'typescript'

// KAN-533 — the ui-kit's copy of apps/dms's KAN-526 guard
// (apps/dms/src/architecture/render-tests-mount-mantine-with-test-theme.test.ts).
// Mantine's useTransition runs two animation frames and a timer per
// transition unless reduced motion is respected; one such timer fired after
// its file's jsdom was gone and failed a fully green run. Every ui-kit test
// therefore mounts Mantine with `testTheme` (./testTheme.ts,
// `respectReducedMotion`).
//
// KAN-543 — both guards read each file with the TypeScript compiler, so
// comments and strings are never code and an import is seen for what it
// binds. In a test (`*.test.*`, `*.spec.*`) or in src/test, every use of
// Mantine's provider must be a `<MantineProvider …>` tag — under whatever
// local name it was imported as — whose `theme` is the `testTheme` imported
// from src/test/testTheme.ts. Any other use (`wrapper: MantineProvider`,
// `createElement(MantineProvider, …)`, a provider handed to a helper) cannot
// be judged and is refused, and so is a `testTheme` declared locally, imported
// from another file, or another export aliased into that name. The two guards
// share this code; a change to one is made to both. Vite reads the sources
// (`?raw`), so the ui-kit needs no Node types for it.

const SOURCES = import.meta.glob<string>(['../**/*.test.ts', '../**/*.test.tsx', '../**/*.spec.ts', '../**/*.spec.tsx', './**/*.ts', './**/*.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
})
const SHARED_TEST_THEME = 'src/test/testTheme'

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
  const passesSharedTheme = (attributes: ts.JsxAttributes): boolean =>
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

describe('ui-kit render tests mount Mantine with the test theme (KAN-533, KAN-543)', () => {
  it('every MantineProvider a test mounts passes testTheme', () => {
    const scanned = Object.entries(SOURCES).map(([key, text]) => [normalise(`src/test/${key}`), text] as const)
    const offences = scanned.flatMap(([path, text]) => providersWithoutTestTheme(text, path).map((o) => `${path}:${o}`))
    // The ui-kit has ~25 test files today, 16 of them mounting their own provider.
    expect(scanned.length).toBeGreaterThan(20)
    expect(scanned.filter(([, text]) => text.includes('<MantineProvider')).length).toBeGreaterThan(10)
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

  it('goes red for a tag with another theme or none', () => {
    expect(providersWithoutTestTheme(`${imports}<MantineProvider theme={theme}>x</MantineProvider>`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}<MantineProvider>x</MantineProvider>`)).toHaveLength(1)
    expect(providersWithoutTestTheme(`${imports}<MantineProvider {...props} />`)).toHaveLength(1)
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
