import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../api/client'
import type { ReferenceValuePage, ReferenceValueRead } from '../api/types'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../i18n'

/** `{ value, label }` for a Mantine `Select`. */
export interface ReferenceValueOption {
  value: string
  label: string
}

export const LABEL_FIELD: Record<SupportedLanguage, 'labelDe' | 'labelFr' | 'labelIt' | 'labelEn'> = {
  de: 'labelDe',
  fr: 'labelFr',
  it: 'labelIt',
  en: 'labelEn',
}

// A list page caps at 100 rows (Settings.pagination_max_limit), so walk the cursor.
export async function fetchActiveReferenceValues(listCode: string): Promise<ReferenceValueRead[]> {
  const rows: ReferenceValueRead[] = []
  let cursor: string | null = null
  do {
    const params = new URLSearchParams({ active: 'true', limit: '100' })
    if (cursor) params.set('cursor', cursor)
    const page = await api.get<ReferenceValuePage>(
      `/reference-data/${encodeURIComponent(listCode)}?${params.toString()}`,
    )
    rows.push(...page.items)
    cursor = page.nextCursor
  } while (cursor)
  return rows
}

export function resolveLanguage(raw: string): SupportedLanguage {
  return (SUPPORTED_LANGUAGES as readonly string[]).includes(raw) ? (raw as SupportedLanguage) : 'en'
}

/**
 * The active values of one reference list, labelled in the UI language and
 * sorted by the list's own `sortOrder` — the same set the server validates a
 * write against (`get_active_reference_value_codes`). Pass `null` to fetch
 * nothing (a closed dialog). `isMissingList` is a 404 for the list itself —
 * a legacy code group with no reference list — kept apart from any other
 * load failure so the caller can say which one happened.
 *
 * Each label carries its code (`Diesel (diesel)`): the admin picking a value
 * is mapping codes, and two values can share a label across languages. A row
 * whose label for the active language is blank renders as `⚠ <code>` — a
 * missing translation must be loud, per the i18n rule.
 */
export function useReferenceValueOptions(listCode: string | null): {
  options: ReferenceValueOption[]
  isLoading: boolean
  isError: boolean
  isMissingList: boolean
} {
  const { i18n } = useTranslation()
  const language = resolveLanguage(i18n.language)

  const query = useQuery({
    queryKey: ['reference-data', listCode, 'active'],
    queryFn: () => fetchActiveReferenceValues(listCode as string),
    enabled: listCode !== null,
    retry: false,
  })

  const options = useMemo<ReferenceValueOption[]>(() => {
    const field = LABEL_FIELD[language]
    return [...(query.data ?? [])]
      .sort((a, b) => a.sortOrder - b.sortOrder)
      .map((row) => {
        const label = row[field]
        return {
          value: row.valueCode,
          label:
            typeof label === 'string' && label.trim() !== ''
              ? `${label} (${row.valueCode})`
              : `⚠ ${row.valueCode}`,
        }
      })
  }, [query.data, language])

  return {
    options,
    isLoading: query.isLoading,
    isError: query.isError,
    isMissingList: query.error instanceof ApiError && query.error.status === 404,
  }
}
