import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'
import { formatNumber } from '@nexotec/ui-kit'
import de from './locales/de.json'
import fr from './locales/fr.json'
import it from './locales/it.json'
import en from './locales/en.json'

export const SUPPORTED_LANGUAGES = ['de', 'fr', 'it', 'en'] as const
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]

// WP-6c PR-12: "a missing key renders a loud marker and never a German
// fallback" — fallbackLng still covers the narrower case this app has
// always guarded against (a key present in the active language's bundle
// simply not existing, falling through to English rather than silently
// rendering German). parseMissingKeyHandler covers the wider one: a key
// absent from EVERY bundle, active and fallback alike, which i18next's own
// default behaviour renders as the bare key path — legible enough to spot
// in a code review, but not the deliberate, impossible-to-mistake-for-
// content marker this app wants a tester or a screen-reader user to see.
void i18next.use(initReactI18next).init({
  resources: {
    de: { translation: de },
    fr: { translation: fr },
    it: { translation: it },
    en: { translation: en },
  },
  lng: 'de',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
  parseMissingKeyHandler: (key) => `⚠ MISSING I18N KEY: ${key}`,
})

// KAN-160: i18next's built-in `number` formatter is Intl's own number format in the
// active language — fr-CH would render `{{count, number}}` as `12 500`.
// Replace it with the app's one Swiss number formatter, so a count in a
// translated string reads `12'500` in all four languages, like every other
// number on screen and on the printed document. `{{count}}` keeps its raw
// value for plural selection; the format applies to the rendered text only.
// The bundles write every count as `{{count, number}}` (enforced by
// architecture/no-locale-number-format.test.ts).
// A value that is not a number passes through as-is rather than rendering
// "NaN" (i18next does not call formatters for a missing variable at all).
i18next.services.formatter?.add('number', (value) => {
  const n = typeof value === 'number' ? value : Number(value)
  return value != null && value !== '' && Number.isFinite(n) ? formatNumber(n) : String(value ?? '')
})

export default i18next

const SWISS_LOCALE: Record<SupportedLanguage, string> = {
  de: 'de-CH',
  fr: 'fr-CH',
  it: 'it-CH',
  en: 'en-CH',
}

/** Swiss locale tag for the active UI language — FR-13's "de-CH / fr-CH /
 * it-CH / en-CH locales" for Intl date/number/currency formatting. */
export function toSwissLocale(language: SupportedLanguage): string {
  return SWISS_LOCALE[language]
}
