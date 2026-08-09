import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'
import de from './locales/de.json'
import fr from './locales/fr.json'
import it from './locales/it.json'
import en from './locales/en.json'

export const SUPPORTED_LANGUAGES = ['de', 'fr', 'it', 'en'] as const
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]

// FR-13: "No fallback-to-German placeholders in production" — fallbackLng
// stays 'en' (the resource bundles are complete for all four, this only
// covers a key nobody has added a translation for yet).
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
