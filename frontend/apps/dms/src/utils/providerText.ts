/**
 * KAN-595 — a provider text in the language asked for. The identAPI delivers an
 * option's name in four languages (`descriptions.de/fr/it/en`); a language it
 * left empty falls back to the delivered default. Provider text is not a
 * locale key, so a missing language is never a loud marker (CLAUDE.md, i18n).
 */
export interface ProviderTexts {
  de?: string | null
  fr?: string | null
  it?: string | null
  en?: string | null
}

export function providerText(texts: ProviderTexts | null | undefined, language: string, fallback: string): string {
  const code = language.slice(0, 2).toLowerCase() as keyof ProviderTexts
  return texts?.[code] || fallback
}
