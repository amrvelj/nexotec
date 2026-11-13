import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  LABEL_FIELD,
  fetchActiveReferenceValues,
  fetchAllReferenceValues,
  resolveLanguage,
} from '../../hooks/useReferenceValueOptions'
import { CODED_REF_LIST_CODES, SPEC_REF_LIST_CODES } from '../../configurationOptions'
import type { ReferenceValueRead } from '../../api/types'

// KAN-43 (C-E) — option_group/equipment_feature join the spec block's own
// reference lists in the one label-lookup query this file already has;
// nothing here needs its own fetch.
// KAN-211 — and the two colour-type lists the advisor picks from.
const OPTION_REF_LIST_CODES = [...SPEC_REF_LIST_CODES, 'option_group', 'equipment_feature', 'colour_type', 'interior_colour_type']

/** Every value of the spec and option lists, labelled in the UI language.
 * KAN-239 — each list is paged within the API's cap (it once asked
 * `limit=200`, which the API answers 422, and the catch hid it as an empty
 * list). Retired values are kept to label what is stored; pickers filter to
 * `active`. A list that failed to load is `null`, so its field can say so; a
 * value with no label in this language renders `⚠ <code>`. */
export function useReferenceLabels() {
  const { i18n } = useTranslation()
  const field = LABEL_FIELD[resolveLanguage(i18n.language)]
  return useQuery({
    queryKey: ['configurator-ref-labels', OPTION_REF_LIST_CODES, field],
    queryFn: async () => {
      const entries = await Promise.all(
        OPTION_REF_LIST_CODES.map(async (code) => {
          try {
            const rows = await fetchAllReferenceValues(code)
            return [
              code,
              rows.map((v) => ({
                valueCode: v.valueCode,
                label: v[field]?.trim() ? v[field] : `⚠ ${v.valueCode}`,
                active: v.active,
              })),
            ] as const
          } catch {
            return [code, null] as const
          }
        }),
      )
      return Object.fromEntries(entries) as Record<string, { valueCode: string; label: string; active: boolean }[] | null>
    },
  })
}

/** KAN-96 — the active values of the five coded fields' lists, paged within
 * the API's cap. A list that fails to load is `null`, so its field can say
 * so instead of silently offering nothing. */
export function useCodedFieldValues() {
  return useQuery({
    queryKey: ['reference-data', 'configurator-coded-fields', 'active'],
    queryFn: async () => {
      const entries = await Promise.all(
        CODED_REF_LIST_CODES.map(async (code) => {
          try {
            return [code, await fetchActiveReferenceValues(code)] as const
          } catch {
            return [code, null] as const
          }
        }),
      )
      return Object.fromEntries(entries) as Record<string, ReferenceValueRead[] | null>
    },
  })
}
