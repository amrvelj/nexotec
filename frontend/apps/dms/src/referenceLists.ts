/**
 * The canonical reference lists a `platform_admin` administers on
 * `/settings/reference` (UI spec Screen Inventory, FR-V-11).
 *
 * **Hardcoded on purpose, for now.** There is no `GET /v1/reference-data`
 * that enumerates the list codes — only `GET /v1/reference-data/{list_code}`
 * for the values of one known list — and `reference_list` carries nothing
 * but the code (no label, no description). A dealer never creates a list,
 * only values; the set below is frozen by spec and by the seed migrations
 * that create it:
 *   - WP-1 shell seed        (alembic c9654d846ac9)  — 8 lists
 *   - vehicle catalogue seed (alembic platform/6ba0a99ed5c4) — 16 lists
 * PRD-Vehicles §"Canonical lists to administer (v1)" names them too.
 *
 * A follow-up ticket adds the read endpoint so this stops being a
 * client-side constant; until then, a list added server-side does not
 * appear here until this array is updated.
 */
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
