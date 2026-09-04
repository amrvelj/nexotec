import type { ReferenceValueRead } from '../api/types'
import { DEFAULT_REFERENCE_LIST, isReferenceListCode, type ReferenceListCode } from '../referenceLists'

export const LANGUAGE_FIELDS = ['labelDe', 'labelFr', 'labelIt', 'labelEn'] as const
export type LanguageField = (typeof LANGUAGE_FIELDS)[number]

/**
 * The set of language fields that are missing on a row.
 *
 * "A missing language is a ROW ERROR, shown on the row — not a silent
 * empty cell" (ticket / UI spec Screen Inventory). The API model makes
 * every `label_*` NOT NULL and rejects a blank on create/update, so this
 * fires only defensively — a `GET` that ever returns a blank (data drift,
 * a future schema change making a label optional) is flagged loudly here
 * instead of rendering as an empty cell.
 */
export function referenceRowLanguageErrors(row: ReferenceValueRead): Set<LanguageField> {
  const missing = new Set<LanguageField>()
  for (const field of LANGUAGE_FIELDS) {
    const value = row[field]
    if (typeof value !== 'string' || value.trim() === '') missing.add(field)
  }
  return missing
}

export interface ReferenceView {
  list: ReferenceListCode
  query: string
}

/**
 * The screen's shareable state, read back out of the URL — ADR-056, "a
 * pasted URL reproduces the view". An unknown or absent `list` falls back
 * to the default rather than 404-ing the whole screen.
 */
export function deriveReferenceView(params: URLSearchParams): ReferenceView {
  const listParam = params.get('list')
  return {
    list: isReferenceListCode(listParam) ? listParam : DEFAULT_REFERENCE_LIST,
    query: params.get('q') ?? '',
  }
}

/** Client-side filter for the reference-list table: value code or any of
 * the four labels contains the query (case-insensitive). */
export function referenceRowMatchesQuery(row: ReferenceValueRead, rawQuery: string): boolean {
  const q = rawQuery.trim().toLowerCase()
  if (!q) return true
  if (row.valueCode.toLowerCase().includes(q)) return true
  return LANGUAGE_FIELDS.some((field) => {
    const value = row[field]
    return typeof value === 'string' && value.toLowerCase().includes(q)
  })
}
