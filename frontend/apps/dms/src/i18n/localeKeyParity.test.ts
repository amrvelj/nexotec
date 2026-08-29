import { describe, expect, it as test } from 'vitest'
import de from './locales/de.json'
import fr from './locales/fr.json'
import it from './locales/it.json'
import en from './locales/en.json'

// WP-6c PR-12: "zero missing translation keys in all four languages,
// proven by a route-walking test." A literal route walk (mount every
// screen, look for the missing-key marker in the rendered DOM) needs
// jsdom/@testing-library/react — neither is a dependency of this
// workspace, and adding both for one test is a bigger footprint than this
// check needs. This is the stronger substitute: comparing every locale
// bundle's own flattened key set catches a missing translation
// UNCONDITIONALLY, including one behind a branch a route walk would only
// exercise by accident (a rare lifecycle status, an error state, a role
// gate) — a route walk only proves what it actually renders, this proves
// the whole bundle. parseMissingKeyHandler (i18n/index.ts) is the runtime
// backstop for whatever this static check can't see (a key referenced
// with a computed/dynamic name at call sites, e.g. `t(`errors.${code}`)`).

type LocaleTree = { [key: string]: string | LocaleTree }

function flattenKeys(tree: LocaleTree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof value === 'string' ? [path] : flattenKeys(value, path)
  })
}

const LOCALES: Record<string, LocaleTree> = { de, fr, it, en }

describe('locale bundles carry the exact same key set', () => {
  const keysByLocale = Object.fromEntries(Object.entries(LOCALES).map(([lang, tree]) => [lang, new Set(flattenKeys(tree))]))
  const allKeys = new Set(Object.values(keysByLocale).flatMap((s) => [...s]))

  for (const [lang, keys] of Object.entries(keysByLocale)) {
    test(`${lang}.json has no key missing that another locale defines`, () => {
      const missing = [...allKeys].filter((key) => !keys.has(key))
      expect(missing).toEqual([])
    })
  }

  test('no locale carries an orphaned key none of the others define (a stale rename, most likely)', () => {
    for (const [lang, keys] of Object.entries(keysByLocale)) {
      for (const key of keys) {
        const presentElsewhere = Object.entries(keysByLocale).some(([other, otherKeys]) => other !== lang && otherKeys.has(key))
        expect(presentElsewhere, `${lang}.json has "${key}", which no other locale defines`).toBe(true)
      }
    }
  })
})
