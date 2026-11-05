import type { Language } from '../api/types'

// KAN-343 (Anto, 2026-11-03, option A): the four languages are named in
// themselves for every user — a French user reads «Deutsch», not «Allemand».
// This is the one ruled exception to "no hardcoded user-visible string"
// (.claude/rules/ui.md, i18n): an endonym reads the same in every UI language,
// so it has no translation to look up. Every screen that names one of the four
// languages as a reference-data column or a customer's correspondence language
// takes it from here; a second literal list is the defect this file removes.
export const LANGUAGE_ENDONYMS: Readonly<Record<Language, string>> = {
  de: 'Deutsch',
  fr: 'Français',
  it: 'Italiano',
  en: 'English',
}

// The display order everywhere: DE, FR, IT, EN.
export const LANGUAGE_ORDER: readonly Language[] = ['de', 'fr', 'it', 'en']

export const LANGUAGE_OPTIONS: { value: Language; label: string }[] = LANGUAGE_ORDER.map((value) => ({
  value,
  label: LANGUAGE_ENDONYMS[value],
}))

// A stored value outside the four (an old row) renders as stored.
export function languageEndonym(code: string): string {
  return (LANGUAGE_ENDONYMS as Record<string, string>)[code] ?? code
}
