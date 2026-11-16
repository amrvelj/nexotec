import { describe, expect, it as test } from 'vitest'
import de from './locales/de'
import fr from './locales/fr'
import it from './locales/it'

// KAN-742 / KAN-171 (Anto): DE, FR and IT person nouns use the generic masculine — no pair
// ("Geschäftsführerin oder ein Geschäftsführer", "Kolleginnen und Kollegen"), no slash, colon,
// asterisk or underscore form ("Kund:innen", "Berater/-in"), no parenthesis ("gérant(e)") and no
// point médian ("conseiller·ère"). This walks every value of the three bundles, so a new string
// with a paired form fails here instead of reading differently from every other screen.

type LocaleTree = { [key: string]: string | LocaleTree }

function flattenValues(tree: LocaleTree, prefix = ''): [string, string][] {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof value === 'string' ? [[path, value] as [string, string]] : flattenValues(value, path)
  })
}

// A feminine form, a conjunction, then the masculine of the same stem: "Kundin oder Kunde",
// "Kolleginnen und Kollegen", "Geschäftsführerin oder ein Geschäftsführer".
const GERMAN_PAIR = /\b([A-ZÄÖÜ][a-zäöüß]{2,})in(nen)? (oder|und|bzw\.?) (eine?n? |der |die |den )?\1/u
const GERMAN_GENDER_MARK = /[a-zäöüß](\*|:|_|\/-?)in(nen)?\b|[a-zäöüß]Innen\b|[a-zäöüß]\(in(nen)?\)/u
const FRENCH_MARK = /\p{L}\((e|ne|se|ère|euse|rice)\)|\p{L}·\p{L}|\b(\p{L}+)(eur|ant|ier|ien) ou (une )?\2(euse|ante|ière|ienne|rice)\b/u
const ITALIAN_MARK = /\p{L}o\/a\b|\p{L}i\/e\b|\p{L}\((a|e)\)/u

const CASES: [string, LocaleTree, RegExp[]][] = [
  ['de', de, [GERMAN_PAIR, GERMAN_GENDER_MARK]],
  ['fr', fr, [FRENCH_MARK]],
  ['it', it, [ITALIAN_MARK]],
]

describe('person nouns use the generic masculine (KAN-171)', () => {
  for (const [lang, tree, patterns] of CASES) {
    test(`${lang}: no paired, slashed, bracketed or point-médian gender form`, () => {
      const hits = flattenValues(tree)
        .filter(([, value]) => patterns.some((pattern) => pattern.test(value)))
        .map(([key, value]) => `${key}: ${value}`)
      expect(hits).toEqual([])
    })
  }

  test('the guard recognises the forms it forbids', () => {
    expect(GERMAN_PAIR.test('Das erledigt eine Geschäftsführerin oder ein Geschäftsführer.')).toBe(true)
    expect(GERMAN_PAIR.test('Ihre Kolleginnen und Kollegen arbeiten weiter.')).toBe(true)
    expect(GERMAN_GENDER_MARK.test('Kund:innen')).toBe(true)
    expect(GERMAN_GENDER_MARK.test('Berater/-in')).toBe(true)
    expect(FRENCH_MARK.test('un gérant(e)')).toBe(true)
    expect(FRENCH_MARK.test('conseiller·ère')).toBe(true)
    expect(ITALIAN_MARK.test('un consulento/a')).toBe(true)
    expect(ITALIAN_MARK.test('il gerente(a)')).toBe(true)
    expect(GERMAN_PAIR.test('Typenschein oder ein Werkscode')).toBe(false)
    expect(GERMAN_PAIR.test('Ihre Kollegen arbeiten weiter.')).toBe(false)
  })
})
