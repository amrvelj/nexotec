import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { LABEL_FIELD, fetchActiveReferenceValues, resolveLanguage } from '../../hooks/useReferenceValueOptions'

/**
 * The active values of a platform reference list as a dealer-facing
 * dropdown: the label in the UI language alone (the admin screen's
 * `label (code)` form is for mapping codes, not for choosing a legal form).
 * A value whose label is blank renders the loud marker, never a fallback.
 */
export function useListOptions(listCode: string): { options: { value: string; label: string }[]; isLoading: boolean } {
  const { i18n } = useTranslation()
  const field = LABEL_FIELD[resolveLanguage(i18n.language)]
  const query = useQuery({
    queryKey: ['reference-data', listCode, 'active'],
    queryFn: () => fetchActiveReferenceValues(listCode),
    retry: false,
  })
  const options = useMemo(
    () =>
      [...(query.data ?? [])]
        .sort((a, b) => a.sortOrder - b.sortOrder)
        .map((row) => {
          const label = row[field]
          return { value: row.valueCode, label: typeof label === 'string' && label.trim() !== '' ? label : `⚠ ${row.valueCode}` }
        }),
    [query.data, field],
  )
  return { options, isLoading: query.isLoading }
}
