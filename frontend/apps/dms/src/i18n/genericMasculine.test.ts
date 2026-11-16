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

// A pair of one stem, in either order: «Geschäftsführerin oder ein Geschäftsführer», «Kunden und
// Kundinnen», «der Verkäufer oder die Verkäuferin». The second word repeats the first one's first
// four letters, and one of the two is the feminine -in/-innen.
const GERMAN_PAIR =
  /\b([A-ZÄÖÜ][a-zäöüß]{3})[a-zäöüß]*in(nen)? (oder|und|bzw\.?) ([a-zäöü]+ )?\1[a-zäöüß]*\b|\b([A-ZÄÖÜ][a-zäöüß]{3})[a-zäöüß]* (oder|und|bzw\.?) ([a-zäöü]+ )?\5[a-zäöüß]*in(nen)?\b/u
const GERMAN_GENDER_MARK = /[a-zäöüß](\*|:|_|\/-?)in(nen)?\b|[a-zäöüß]Innen\b|[a-zäöüß]\(in(nen)?\)/u
// The neutral participle used for people («Mitarbeitende», «Teilnehmende», «Studierende»).
const GERMAN_NEUTRAL_PARTICIPLE = /(Mitarbeitende|Teilnehmende|Studierende|Lernende|Lehrende|Mitwirkende|Beschäftigte)n?\b/u
// French: a slash/parenthesis/point-médian form, or a pair of one stem joined by «ou»/«et»
// («le directeur ou la directrice», «conseiller ou conseillère», «collaborateurs et collaboratrices»).
const FRENCH_MARK =
  /\p{L}\((e|ne|se|ère|euse|rice)\)|\p{L}·\p{L}|\b(\p{L}{4})\p{L}* (ou|et) ((le|la|les|un|une|des) )?\2\p{L}*(ice|euse|ère|ienne|ante|eure)s?\b/u
// Italian: «-o/a», «-i/e», «il/la», «(a)», or a pair of one stem («venditore o venditrice»,
// «consulente o la consulente» aside — that is one word).
const ITALIAN_MARK =
  /\p{L}o\/a\b|\p{L}i\/e\b|\b(il|lo|gli)\/(la|le)\b|\p{L}\((a|e)\)|\b(\p{L}{4})\p{L}* (o|e) ((il|la|lo|un|una) )?\4\p{L}*(trice|trici|essa|esse)\b/u

const CASES: [string, LocaleTree, RegExp[]][] = [
  ['de', de, [GERMAN_PAIR, GERMAN_GENDER_MARK, GERMAN_NEUTRAL_PARTICIPLE]],
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
    expect(GERMAN_PAIR.test('Kunden und Kundinnen')).toBe(true)
    expect(GERMAN_PAIR.test('der Verkäufer oder die Verkäuferin')).toBe(true)
    expect(GERMAN_NEUTRAL_PARTICIPLE.test('Nur für Nexotec-Mitarbeitende')).toBe(true)
    expect(FRENCH_MARK.test('le directeur ou la directrice')).toBe(true)
    expect(FRENCH_MARK.test('conseiller ou conseillère')).toBe(true)
    expect(FRENCH_MARK.test('collaborateurs et collaboratrices')).toBe(true)
    expect(ITALIAN_MARK.test('venditore o venditrice')).toBe(true)
    expect(ITALIAN_MARK.test('il/la cliente')).toBe(true)
    expect(GERMAN_PAIR.test('Typenschein oder ein Werkscode')).toBe(false)
    expect(GERMAN_NEUTRAL_PARTICIPLE.test('Nexotec-Mitarbeiter')).toBe(false)
    expect(GERMAN_PAIR.test('Ihre Kollegen arbeiten weiter.')).toBe(false)
  })
})
