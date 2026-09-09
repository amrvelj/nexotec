import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { ReferenceValuePage, ReferenceValueRead } from '../api/types'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../i18n'

/** `{ value, label }` for a Mantine `Select` / the customer `SelectField`. */
export interface CountryOption {
  value: string
  label: string
}

const LABEL_FIELD: Record<SupportedLanguage, 'labelDe' | 'labelFr' | 'labelIt' | 'labelEn'> = {
  de: 'labelDe',
  fr: 'labelFr',
  it: 'labelIt',
  en: 'labelEn',
}

// The `country` reference list is ~250 rows and the list endpoint caps a
// page at 100 (Settings.pagination_max_limit), so walk the cursor. It
// changes essentially never — one long-lived cache entry, shared by the
// create wizard and every customer detail screen.
async function fetchActiveCountries(): Promise<ReferenceValueRead[]> {
  const rows: ReferenceValueRead[] = []
  let cursor: string | null = null
  do {
    const params = new URLSearchParams({ active: 'true', limit: '100' })
    if (cursor) params.set('cursor', cursor)
    const page = await api.get<ReferenceValuePage>(`/reference-data/country?${params.toString()}`)
    rows.push(...page.items)
    cursor = page.nextCursor
  } while (cursor)
  return rows
}

function resolveLanguage(raw: string): SupportedLanguage {
  return (SUPPORTED_LANGUAGES as readonly string[]).includes(raw) ? (raw as SupportedLanguage) : 'en'
}

/**
 * Country options for the active UI language, backed by the `country`
 * reference list (KAN-32) — the same list `nationality` and every
 * `addressCountry` are validated against server-side.
 *
 * A row whose label for the active language is blank renders as `⚠ <CODE>`
 * rather than an empty option: a missing translation must be loud, per the
 * i18n rule, not a silently unselectable gap.
 */
export function useCountryOptions(): {
  options: CountryOption[]
  isLoading: boolean
  isError: boolean
} {
  const { i18n } = useTranslation()
  const language = resolveLanguage(i18n.language)

  const query = useQuery({
    queryKey: ['reference-data', 'country', 'active'],
    queryFn: fetchActiveCountries,
    staleTime: 60 * 60 * 1000,
    gcTime: 24 * 60 * 60 * 1000,
  })

  const options = useMemo<CountryOption[]>(() => {
    const field = LABEL_FIELD[language]
    return (query.data ?? [])
      .map((row) => {
        const label = row[field]
        return {
          value: row.valueCode,
          label: typeof label === 'string' && label.trim() !== '' ? label : `⚠ ${row.valueCode}`,
        }
      })
      .sort((a, b) => a.label.localeCompare(b.label, language))
  }, [query.data, language])

  return { options, isLoading: query.isLoading, isError: query.isError }
}
