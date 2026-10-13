/// <reference types="node" />
// Runs under vitest's node environment and needs fs/path/url — the same
// one-file opt-in as no-hardcoded-colour.test.ts (see its header).
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-322 — a Mantine `<Alert withCloseButton>` renders a close button whose
// only accessible name is the `closeButtonLabel` prop; without it a screen
// reader announces an unnamed button. oxlint has no custom-rule mechanism,
// so this is a build-failing scan of every JSX element that declares
// `withCloseButton` (truthy), exactly like the colour-token scan: it fails
// on any such element without a `closeButtonLabel` in the same element.
//
// Scope: every `.tsx` under frontend/ (ui-kit included — it has no test
// runner of its own in CI), test files excluded. Only the Mantine
// components that take `closeButtonLabel` are checked (Alert,
// Notification); Modal/Drawer name their close button through
// `closeButtonProps` and are not this rule's concern.

const FRONTEND_ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])
const CHECKED_TAGS = ['Alert', 'Notification']

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (entry.endsWith('.tsx') && !entry.endsWith('.test.tsx')) out.push(full)
  }
  return out
}

/** The text of every JSX opening element `<Tag …>` for the checked tags —
 * props included, children excluded. A prop value in braces may itself
 * contain `>` (`onClose={() => …}`), so the element ends at the first `>`
 * outside braces and outside a quoted attribute value, never at an arrow. */
export function openingElements(source: string, tags: readonly string[] = CHECKED_TAGS): { line: number; text: string }[] {
  const found: { line: number; text: string }[] = []
  const start = new RegExp(`<(${tags.join('|')})\\b`, 'g')
  let match: RegExpExecArray | null
  while ((match = start.exec(source)) !== null) {
    let depth = 0
    let quote: string | null = null
    let i = match.index + match[0].length
    for (; i < source.length; i += 1) {
      const ch = source[i]
      if (quote) {
        if (ch === quote) quote = null
        continue
      }
      if (depth === 0 && (ch === '"' || ch === "'")) {
        quote = ch
        continue
      }
      if (ch === '{') depth += 1
      else if (ch === '}') depth -= 1
      else if (ch === '>' && depth === 0 && source[i - 1] !== '=') break
    }
    const text = source.slice(match.index, i + 1)
    const line = source.slice(0, match.index).split('\n').length
    found.push({ line, text })
    start.lastIndex = i + 1
  }
  return found
}

/** `withCloseButton` as a bare prop or with any value other than the
 * literal `false` (Modal-style `withCloseButton={false}` opts out). */
const WITH_CLOSE_BUTTON = /\bwithCloseButton\b(?!=\{false\})/
const CLOSE_BUTTON_LABEL = /\bcloseButtonLabel=/

export function offendingElements(source: string): { line: number; text: string }[] {
  return openingElements(source).filter(({ text }) => WITH_CLOSE_BUTTON.test(text) && !CLOSE_BUTTON_LABEL.test(text))
}

describe('every dismissable Alert names its close button (KAN-322)', () => {
  it('finds no <Alert withCloseButton> (or <Notification>) without a closeButtonLabel anywhere under frontend/', () => {
    const offenders: string[] = []
    for (const file of collectFiles(FRONTEND_ROOT)) {
      for (const { line, text } of offendingElements(readFileSync(file, 'utf-8'))) {
        offenders.push(`${relative(FRONTEND_ROOT, file)}:${line}: ${text.replace(/\s+/g, ' ').trim()}`)
      }
    }
    expect(offenders).toEqual([])
  })

  // A scanner with a broken element walk would pass on an empty result set
  // — these prove it fires on the real shapes, and stops where it should.
  it('flags a bare withCloseButton without a label, across lines and past an arrow in onClose', () => {
    const source = `
      <Alert
        color="red"
        withCloseButton
        onClose={() => setError(null)}
      >
        {error}
      </Alert>`
    expect(offendingElements(source)).toHaveLength(1)
    expect(offendingElements(source)[0].text).toContain('onClose={() => setError(null)}')
  })

  it('accepts an element that carries closeButtonLabel, and one that opts out with withCloseButton={false}', () => {
    expect(offendingElements(`<Alert withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => x()}>{e}</Alert>`)).toEqual([])
    expect(offendingElements(`<Alert withCloseButton={false} title="x">{e}</Alert>`)).toEqual([])
  })

  it('does not stop the element at a ">" inside a quoted attribute value, and reports the source line', () => {
    const source = `const a = 1\n<Alert title="a > b" withCloseButton onClose={close}>{e}</Alert>`
    const offenders = offendingElements(source)
    expect(offenders).toHaveLength(1)
    expect(offenders[0].line).toBe(2)
    expect(offenders[0].text.endsWith('onClose={close}>')).toBe(true)
  })

  it('ignores a component outside the checked set', () => {
    expect(offendingElements(`<Modal withCloseButton onClose={close}>{e}</Modal>`)).toEqual([])
  })
})
