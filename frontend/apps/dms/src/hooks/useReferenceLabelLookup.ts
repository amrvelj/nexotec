import { useQueries } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { ReferenceValueRead } from '../api/types'
import { LABEL_FIELD, fetchAllReferenceValues, resolveLanguage } from './useReferenceValueOptions'

/** How long a list's labels count as fresh. Labels change only when an admin
 * edits reference data (`ReferenceDataPage`'s `['reference-data', list]`
 * invalidation reaches these keys too, being their prefix). */
const LABELS_STALE_MS = 5 * 60 * 1000

/** Shown while a list's labels are still on their way — never the raw code. */
export const LABEL_PENDING = '…'

export interface ReferenceLabelLookup {
  /** The stored `code`'s label in the UI language. A code with no label in
   * that language, a code its list does not know, and every code of a list
   * that failed to load render `⚠ <code>` — never the raw code silently.
   * `LABEL_PENDING` until the list has loaded. */
  label: (listCode: string, code: string) => string
  isLoading: boolean
}

/**
 * KAN-260 — the shared, cached label lookup for stored reference codes. One
 * query per list, keyed `['reference-data', <list>, 'all']`, so every
 * component that asks for the same list — four summary cards on one screen,
 * say — shares one request. Each list is paged within the API's cap
 * (`fetchAllReferenceValues`) and **includes retired values**: a code
 * retired since it was stored still finds its label. (A picker offers
 * active values only — that is `fetchActiveReferenceValues`, not this.)
 */
export function useReferenceLabelLookup(listCodes: readonly string[]): ReferenceLabelLookup {
  const { i18n } = useTranslation()
  const field = LABEL_FIELD[resolveLanguage(i18n.language)]

  const results = useQueries({
    queries: listCodes.map((listCode) => ({
      queryKey: ['reference-data', listCode, 'all'],
      queryFn: () => fetchAllReferenceValues(listCode),
      staleTime: LABELS_STALE_MS,
      retry: false,
    })),
  })

  const label = (listCode: string, code: string): string => {
    const r = results[listCodes.indexOf(listCode)]
    if (!r || !(r.isSuccess || r.isError)) return LABEL_PENDING
    const text = r.data?.find((row: ReferenceValueRead) => row.valueCode === code)?.[field]
    return typeof text === 'string' && text.trim() !== '' ? text : `⚠ ${code}`
  }

  return { label, isLoading: results.some((r) => r.isLoading) }
}
