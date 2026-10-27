import { describe, expect, it } from 'vitest'
import de from './i18n/locales/de.json'
import en from './i18n/locales/en.json'
import fr from './i18n/locales/fr.json'
import it_ from './i18n/locales/it.json'
import { TECHNICAL_SPEC_GROUPS, specFieldLabelKey } from './configurationOptions'

// KAN-459 — the read-only technical block renders every field of the
// technical registry with a label in the UI language: a key missing from a
// bundle renders a loud marker or an English fallback on the stock detail.

const BUNDLES = { de, en, fr, it: it_ } as Record<string, unknown>

function lookup(bundle: unknown, key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], bundle)
}

const KEYS = [
  'configurator.summary.technicalData',
  'configurator.spec.groups.colours',
  'configurator.colour.exterior',
  'configurator.colour.interior',
  ...TECHNICAL_SPEC_GROUPS.map((g) => g.titleKey),
  ...TECHNICAL_SPEC_GROUPS.flatMap((g) => g.fields.map((f) => specFieldLabelKey(f.key as string))),
]

describe('technical data labels (KAN-459)', () => {
  it.each(Object.keys(BUNDLES))('every technical field has a label in %s', (lang) => {
    const missing = KEYS.filter((key) => {
      const value = lookup(BUNDLES[lang], key)
      return typeof value !== 'string' || value.trim() === ''
    })
    expect(missing).toEqual([])
  })

  it.each(Object.keys(BUNDLES))('a label naming a unit names the one the value prints, in %s', (lang) => {
    // "Anhängelast (kg)" drops its "(kg)" because the value reads "1'600 kg";
    // a label naming another unit ("(cc)") would sit beside "1'984 cm³".
    const wrong = TECHNICAL_SPEC_GROUPS.flatMap((g) => g.fields)
      .filter((f) => f.unit)
      .map((f) => ({ key: f.key, unit: f.unit!, label: lookup(BUNDLES[lang], specFieldLabelKey(f.key as string)) as string }))
      .filter(({ unit, label }) => /\([^)]*\)$/.test(label) && !label.endsWith(` (${unit})`))
    expect(wrong).toEqual([])
  })

  it('leaves the commercial price group out', () => {
    expect(TECHNICAL_SPEC_GROUPS.map((g) => g.titleKey)).not.toContain('configurator.spec.groups.price')
    expect(TECHNICAL_SPEC_GROUPS.flatMap((g) => g.fields.map((f) => f.key))).not.toContain('basePrice')
  })
})
