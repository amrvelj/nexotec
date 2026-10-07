import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { fetchActiveReferenceValues, LABEL_FIELD, resolveLanguage } from './useReferenceValueOptions'

/** `{ value, label }` for a Mantine `Select` / the customer `SelectField`. */
export interface CountryOption {
  value: string
  label: string
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
    // ~250 rows; it changes essentially never — one long-lived cache entry,
    // shared by the create wizard and every customer detail screen.
    queryFn: () => fetchActiveReferenceValues('country'),
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
