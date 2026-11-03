import { describe, expect, it } from 'vitest'

// KAN-533 — the ui-kit's copy of apps/dms's KAN-526 guard
// (apps/dms/src/architecture/render-tests-mount-mantine-with-test-theme.test.ts).
// Mantine's useTransition runs two animation frames and a timer per
// transition unless reduced motion is respected; one such timer fired after
// its file's jsdom was gone and failed a fully green run. Every ui-kit test
// therefore mounts Mantine with `testTheme` (./testTheme.ts,
// `respectReducedMotion`). This refuses a `<MantineProvider` in a test, or in
// src/test, whose `theme` is anything else — the next file that mounts its own
// provider would reopen the leak. Unlike the dms guard it also refuses a
// provider mounted any other way — `wrapper: MantineProvider`,
// `createElement(MantineProvider, …)`, a provider passed around by name —
// and reads `*.spec.*` files too: every `MantineProvider` in a test is an
// import or a `<MantineProvider theme={testTheme}>` tag. Vite reads the
// sources (`?raw`), so the ui-kit needs no Node types for it.

const SOURCES = import.meta.glob<string>(['../**/*.test.ts', '../**/*.test.tsx', '../**/*.spec.ts', '../**/*.spec.tsx', './**/*.ts', './**/*.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
})
const THIS_FILE = './renderTestsMountMantineWithTestTheme.test.ts'

/** Every Mantine provider `text` mounts without `theme={testTheme}`: an
 * opening `<MantineProvider …>` tag without it, and any other use of the name
 * outside an import, a closing tag or a comment (a `wrapper:`, a
 * `createElement`, a provider handed to a helper — none can be judged). */
export function providersWithoutTestTheme(text: string): string[] {
  const offences: string[] = []
  for (const match of text.matchAll(/<MantineProvider\b([^>]*)>/g)) {
    if (!/\btheme=\{testTheme\}/.test(match[1])) offences.push(match[0])
  }
  const rest = text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/^import[^;]*?from\s*['"][^'"]+['"];?/gms, '')
    .replace(/<\/?MantineProvider\b[^>]*>/g, '')
  for (const match of rest.matchAll(/[^\n]*\bMantineProvider\b[^\n]*/g)) offences.push(match[0].trim())
  return offences
}

describe('ui-kit render tests mount Mantine with the test theme (KAN-533)', () => {
  it('every MantineProvider a test mounts passes testTheme', () => {
    const scanned = Object.entries(SOURCES).filter(([path]) => path !== THIS_FILE && !path.endsWith('/test/renderTestsMountMantineWithTestTheme.test.ts'))
    const offences = scanned.flatMap(([path, text]) => providersWithoutTestTheme(text).map((tag) => `${path}: ${tag}`))
    // The ui-kit has ~25 test files today, 16 of them mounting their own provider.
    expect(scanned.length).toBeGreaterThan(20)
    expect(scanned.filter(([, text]) => text.includes('<MantineProvider')).length).toBeGreaterThan(10)
    expect(offences).toEqual([])
  })

  it('goes red for a provider with another theme or none', () => {
    expect(providersWithoutTestTheme('<MantineProvider theme={theme}>')).toHaveLength(1)
    expect(providersWithoutTestTheme('<MantineProvider>')).toHaveLength(1)
    expect(providersWithoutTestTheme('<MantineProvider theme={theme} env="test">')).toHaveLength(1)
    expect(providersWithoutTestTheme('<MantineProvider env="test" theme={testTheme} withCssVariables={false}>')).toEqual([])
    expect(providersWithoutTestTheme('renderHook(() => useX(), { wrapper: MantineProvider })')).toHaveLength(1)
    expect(providersWithoutTestTheme('render(createElement(MantineProvider, { theme }, ui))')).toHaveLength(1)
    expect(
      providersWithoutTestTheme(
        'import { Button, MantineProvider } from "@mantine/core";\n// a MantineProvider in a comment\nrender(<MantineProvider theme={testTheme}><Button /></MantineProvider>)',
      ),
    ).toEqual([])
  })
})
