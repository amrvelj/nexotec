import i18next, { type BackendModule, type ResourceKey } from 'i18next'
import { initReactI18next } from 'react-i18next'
import { formatNumber } from '@nexotec/ui-kit'
import de from './locales/de.json'
import en from './locales/en.json'

export const SUPPORTED_LANGUAGES = ['de', 'fr', 'it', 'en'] as const
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]

// KAN-438: the app starts in German (`lng` below) and falls back to English,
// so those two bundles ship with the app; French and Italian (~100 kB each)
// are fetched, as their own chunks, the first time a user switches to them.
// `changeLanguage` waits for the bundle before it switches, so a switch never
// shows keys or a half-translated screen — the old language stays until the
// new one is complete. A bundle that cannot be fetched (offline, a chunk
// removed by a deploy) is retried by i18next and then falls back to English,
// as a key missing from the active bundle always has. Exported only so a
// render test can hold one bundle back (onDemandBundles.render.test.tsx).
export const onDemandBundleLoaders: Partial<Record<SupportedLanguage, () => Promise<{ default: ResourceKey }>>> = {
  fr: () => import('./locales/fr.json'),
  it: () => import('./locales/it.json'),
}

const onDemandBundles: BackendModule = {
  type: 'backend',
  init: () => {},
  read: (language, _namespace, callback) => {
    const load = onDemandBundleLoaders[language as SupportedLanguage]
    if (!load) {
      callback(new Error(`no locale bundle for ${language}`), false)
      return
    }
    load().then(
      (bundle) => callback(null, bundle.default),
      (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), false),
    )
  },
}

// WP-6c PR-12: "a missing key renders a loud marker and never a German
// fallback" — fallbackLng still covers the narrower case this app has
// always guarded against (a key present in the active language's bundle
// simply not existing, falling through to English rather than silently
// rendering German). parseMissingKeyHandler covers the wider one: a key
// absent from EVERY bundle, active and fallback alike, which i18next's own
// default behaviour renders as the bare key path — legible enough to spot
// in a code review, but not the deliberate, impossible-to-mistake-for-
// content marker this app wants a tester or a screen-reader user to see.
void i18next.use(onDemandBundles).use(initReactI18next).init({
  resources: {
    de: { translation: de },
    en: { translation: en },
  },
  // The two bundled languages are used as given; the backend is asked only
  // for a language with no bundle yet (KAN-438).
  partialBundledLanguages: true,
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
