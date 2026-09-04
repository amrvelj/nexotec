/**
 * The canonical reference lists a `platform_admin` administers on
 * `/settings/reference` (UI spec Screen Inventory, FR-V-11).
 *
 * The authoritative set — codes, localised names and value counts — comes
 * from `GET /v1/reference-data`; `useReferenceLists()` below fetches it and
 * `referenceListOptions()` turns it into the picker's options.
 *
 * `REFERENCE_LIST_CODES` stays as a **fallback only**: the frozen set the
 * seed migrations create, used to render the picker (code as its own label)
 * until the fetch resolves and to keep `deriveReferenceView`'s URL guard
 * synchronous. It is not the source of truth for what the screen shows.
 *   - WP-1 shell seed        (alembic c9654d846ac9)        —  8 lists
 *   - vehicle catalogue seed (alembic platform/6ba0a99ed5c4) — 16 lists
 * A dealer never creates a list, only values, so this set only ever grows
 * by a migration — and when it does, the endpoint reflects it immediately
 * while this constant lags until updated.
 */
import { useQuery } from '@tanstack/react-query'

import { api } from './api/client'
import type { ReferenceListCollection, ReferenceListRead } from './api/types'
import type { SupportedLanguage } from './i18n'

export const REFERENCE_LIST_CODES = [
  // WP-1 shell seed (c9654d846ac9)
  'vehicle_type',
  'fuel_type',
  'body_style',
  'drivetrain',
  'transmission',
  'exterior_color',
  'interior_color',
  'oem_affiliations',
  // vehicle catalogue seed (platform/6ba0a99ed5c4)
  'vehicle_kind',
  'vehicle_class',
  'colour',
  'colour_type',
  'tyre_type',
  'axle_position',
  'option_group',
  'equipment_feature',
  'accessory_type',
  'energy_efficiency_category',
  'emission_standard',
  'consumption_norm',
  'registration_status',
  'vehicle_status',
  'custody_event_type',
  'party_role',
] as const

export type ReferenceListCode = (typeof REFERENCE_LIST_CODES)[number]

export const DEFAULT_REFERENCE_LIST: ReferenceListCode = 'fuel_type'

export function isReferenceListCode(value: string | null | undefined): value is ReferenceListCode {
  return value != null && (REFERENCE_LIST_CODES as readonly string[]).includes(value)
}

const LABEL_FIELD: Record<SupportedLanguage, 'labelDe' | 'labelFr' | 'labelIt' | 'labelEn'> = {
  de: 'labelDe',
  fr: 'labelFr',
  it: 'labelIt',
  en: 'labelEn',
}

/** A list's display name in the active UI language. The customer's
 * correspondence language never enters here — this is a UI-chrome label.
 * Falls back English → code so a picker entry is never blank. */
export function referenceListLabel(item: ReferenceListRead, language: SupportedLanguage): string {
  return item[LABEL_FIELD[language]] || item.labelEn || item.listCode
}

/** Options for the `<Select>` list picker: the server's lists once loaded
 * (localised, in the server's order), the frozen fallback set (code as its
 * own label) until then. */
export function referenceListOptions(
  items: ReferenceListRead[] | undefined,
  language: SupportedLanguage,
): { value: string; label: string }[] {
  if (items && items.length > 0) {
    return items.map((item) => ({ value: item.listCode, label: referenceListLabel(item, language) }))
  }
  return REFERENCE_LIST_CODES.map((code) => ({ value: code, label: code }))
}

/** The canonical lists. Open to any authenticated principal, so no role
 * gate here — the screen renders whatever comes back. */
export function useReferenceLists() {
  return useQuery({
    queryKey: ['reference-lists'],
    queryFn: () => api.get<ReferenceListCollection>('/reference-data'),
    staleTime: 5 * 60 * 1000, // seed-only data — no need to refetch often
  })
}
