/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-526 — Mantine's useTransition runs two animation frames and a timer
// per transition unless reduced motion is respected; one such timer fired
// after its file's jsdom was gone and failed a fully green run. Every render
// test therefore mounts Mantine with `testTheme` (src/test/testTheme.ts,
// `respectReducedMotion`), through renderWithProviders or its own provider.
// This refuses a `<MantineProvider` in a test, or in src/test, whose `theme`
// is anything else — the next file that mounts its own provider would reopen
// the leak.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const THIS_FILE = fileURLToPath(import.meta.url)

function files(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) files(full, out)
    else if (/\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

/** Every `<MantineProvider …>` opening tag in `text` whose props do not pass `theme={testTheme}`. */
export function providersWithoutTestTheme(text: string): string[] {
  const offences: string[] = []
  for (const match of text.matchAll(/<MantineProvider\b([^>]*)>/g)) {
    if (!/\btheme=\{testTheme\}/.test(match[1])) offences.push(match[0])
  }
  return offences
}

describe('render tests mount Mantine with the test theme (KAN-526)', () => {
  it('every MantineProvider a test mounts passes testTheme', () => {
    const scanned = files(SRC_ROOT).filter((f) => f !== THIS_FILE && (/\.test\.tsx?$/.test(f) || relative(SRC_ROOT, f).startsWith(`test${'/'}`)))
    const offences = scanned.flatMap((f) => providersWithoutTestTheme(readFileSync(f, 'utf8')).map((tag) => `${relative(SRC_ROOT, f)}: ${tag}`))
    expect(scanned.length).toBeGreaterThan(100)
    expect(offences).toEqual([])
  })

  it('goes red for a provider with another theme or none', () => {
    expect(providersWithoutTestTheme('<MantineProvider theme={theme}>')).toHaveLength(1)
    expect(providersWithoutTestTheme('<MantineProvider>')).toHaveLength(1)
    expect(providersWithoutTestTheme('<MantineProvider env="test" theme={testTheme} withCssVariables={false}>')).toEqual([])
  })
})
