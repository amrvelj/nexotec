import type { StandardTextAppliesTo, StandardTextCategory, StandardTextRead, StandardTextSlot } from '../../api/types'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'

/**
 * KAN-208 — the library as the screen shows it: one entry per text code,
 * each language holding the platform seed and the Betrieb's own row (an
 * adaptation of the seed, or a text of its own). Location variants are the
 * API's (no Locations screen yet); this screen edits the Betrieb's scope.
 */

export const TEXT_LANGUAGES: readonly SupportedLanguage[] = SUPPORTED_LANGUAGES

export interface LanguageVersion {
  seed: StandardTextRead | null
  own: StandardTextRead | null
}

export interface TextEntry {
  code: string
  category: StandardTextCategory
  appliesTo: StandardTextAppliesTo
  slot: StandardTextSlot
  languages: Record<SupportedLanguage, LanguageVersion>
}

/** `template` — the seed only (labelled «Vorlage — bitte prüfen», FR-A-08);
 * `adapted` — the Betrieb adapted the seed; `own` — the Betrieb's own text. */
export type EntryKind = 'template' | 'adapted' | 'own'

const emptyLanguages = (): Record<SupportedLanguage, LanguageVersion> =>
  Object.fromEntries(TEXT_LANGUAGES.map((language) => [language, { seed: null, own: null }])) as Record<SupportedLanguage, LanguageVersion>

export function groupTexts(items: readonly StandardTextRead[]): TextEntry[] {
  const byCode = new Map<string, TextEntry>()
  for (const row of items) {
    if (row.scope === 'location') continue
    let entry = byCode.get(row.code)
    if (!entry) {
      entry = { code: row.code, category: row.category, appliesTo: row.appliesTo, slot: row.slot, languages: emptyLanguages() }
      byCode.set(row.code, entry)
    }
    const version = entry.languages[row.language as SupportedLanguage]
    if (!version) continue
    if (row.source === 'platform_seed') version.seed = row
    else version.own = row
  }
  return [...byCode.values()].sort(
    (a, b) => a.appliesTo.localeCompare(b.appliesTo) || a.slot.localeCompare(b.slot) || a.code.localeCompare(b.code),
  )
}

/** The row a language prints: the Betrieb's own, else the seed. */
export function effective(entry: TextEntry, language: SupportedLanguage): StandardTextRead | null {
  const version = entry.languages[language]
  return version.own ?? version.seed
}

export function entryKind(entry: TextEntry): EntryKind {
  const versions = TEXT_LANGUAGES.map((language) => entry.languages[language])
  if (!versions.some((v) => v.seed)) return 'own'
  return versions.some((v) => v.own) ? 'adapted' : 'template'
}

/** FR-A-29: a seed this entry adapted changed since the Betrieb reviewed it. */
export function seedChanged(entry: TextEntry): boolean {
  return TEXT_LANGUAGES.some((language) => entry.languages[language].own?.seedChanged === true)
}

/** The title in the user's language, else the first language that has one. */
export function entryTitle(entry: TextEntry, uiLanguage: SupportedLanguage): string {
  const preferred = effective(entry, uiLanguage)
  if (preferred) return preferred.title
  for (const language of TEXT_LANGUAGES) {
    const row = effective(entry, language)
    if (row) return row.title
  }
  return entry.code
}

/** A placeholder token's i18n key (the token has a dot; the key may not). */
export const placeholderKey = (token: string) => `standardTexts.placeholder.${token.replace('.', '_')}`
