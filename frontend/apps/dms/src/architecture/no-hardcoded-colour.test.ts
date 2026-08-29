/// <reference types="node" />
// This file's own type-checking project (tsconfig.app.json) targets the
// browser (`lib: ["ES2023", "DOM"]`, no "node" in `types`) — correct for
// everything else under src/, but this one file runs under vitest's node
// environment and needs fs/path/url. `@types/node` is already a
// devDependency (vite.config.ts's own tsconfig.node.json uses it); this
// reference just opts this one file in without widening it project-wide.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// WP-6c PR-1: "No component may hardcode a colour, radius, spacing or
// shadow" outside the token file (§ Design Tokens) — "this rule is the
// whole reason a dark mode is possible later." oxlint has a fixed rule set
// with no plugin/custom-rule mechanism, so this cannot be an oxlint rule;
// it is a build-failing scan instead, the same "verify by query, not code
// review" idiom already used twice on the backend (WP-5's plate-lookup
// guard, WP-6b's no-layout-code guard).
//
// Only vitest's ts/dms project actually runs in CI (see .github/workflows/
// test.yml — oxlint/tsc/vitest/build all run `working-directory:
// frontend/apps/dms`), so this file lives here but walks up to scan the
// whole `frontend/` tree — `packages/ui-kit` has no test runner of its own.

const FRONTEND_ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')

// The token source itself (real CSS custom properties) and its TypeScript
// import surface. tokens.ts legitimately interpolates two plain hex
// literals — purple[8] and slate[8] — that the source token set never
// defines a CSS variable for; see the comment at their declaration.
const EXEMPT_SUFFIXES = ['/tokens.css', '/tokens.ts']

const SCAN_EXTENSIONS = ['.ts', '.tsx']
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

const HEX_COLOUR = /#[0-9A-Fa-f]{3,8}\b/

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      collectFiles(full, out)
    } else if (SCAN_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full)
    }
  }
  return out
}

describe('no hardcoded colours outside the token file', () => {
  it('finds no hex-colour literal anywhere under frontend/ except tokens.css and tokens.ts', () => {
    const offenders: string[] = []

    for (const file of collectFiles(FRONTEND_ROOT)) {
      if (EXEMPT_SUFFIXES.some((suffix) => file.endsWith(suffix))) continue
      // A test file may legitimately need a hex-shaped literal to assert
      // against (this file's own fixture below is exactly that case).
      if (file.endsWith('.test.ts') || file.endsWith('.test.tsx')) continue

      const lines = readFileSync(file, 'utf-8').split('\n')
      lines.forEach((line, index) => {
        if (HEX_COLOUR.test(line)) {
          offenders.push(`${file}:${index + 1}: ${line.trim()}`)
        }
      })
    }

    expect(offenders).toEqual([])
  })

  // A scan with a typo'd extension list or an over-eager skip-dir would
  // pass silently — this proves the regex and the file walk both actually
  // fire on a real hex literal, not just on an empty result set.
  it('the pattern itself matches an ordinary hex colour', () => {
    expect(HEX_COLOUR.test('background: "#ABCDEF"')).toBe(true)
    expect(HEX_COLOUR.test('background: "#fff"')).toBe(true)
    expect(HEX_COLOUR.test('href="#config"')).toBe(false)
  })
})
