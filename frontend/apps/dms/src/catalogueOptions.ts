import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type { CatalogueBrowseMode, CatalogueFacetsRead } from './api/types'

/** Mode → the production-year default (PRD `NurNeue`): `build` shows only
 * in-production ranges, `record` shows everything. */
export function catalogueModeOptions(t: (k: string) => string): { value: CatalogueBrowseMode; label: string }[] {
  return [
    { value: 'build', label: t('catalogueBrowse.mode.build') },
    { value: 'record', label: t('catalogueBrowse.mode.record') },
  ]
}

/** Facet field (camelCase, as the API returns it) → the canonical
 * reference list its value codes come from, so the grid can show
 * translated labels instead of raw codes. */
export const FACET_LIST_CODE: Record<string, string> = {
  fuelType: 'fuel_type',
  bodyStyle: 'body_style',
  drivetrain: 'drivetrain',
  transmission: 'transmission',
  vehicleKind: 'vehicle_kind',
  vehicleClass: 'vehicle_class',
  emissionStandard: 'emission_standard',
  engineCycle: 'engine_cycle',
}

const NUMERIC_FACET_FIELDS = [
  'ps',
  'kw',
  'displacementCcm',
  'doors',
  'seats',
  'co2Gkm',
  'modelYearFrom',
  'basePrice',
] as const

/**
 * The FilterBuilder field list, derived from what the mirror actually
 * holds for the current drill-down scope — `facets` is `null` while it
 * loads, in which case only the always-safe numeric fields are offered.
 * A coded field with no values in scope is dropped, so a user never picks
 * an option that returns nothing (PRD FR-C-01).
 */
export function buildCatalogueFilterFields(
  t: (k: string) => string,
  facets: CatalogueFacetsRead | undefined,
  labelFor: (listCode: string, valueCode: string) => string,
): FilterFieldDef[] {
  const fields: FilterFieldDef[] = []

  for (const [field, listCode] of Object.entries(FACET_LIST_CODE)) {
    const values = facets?.coded?.[field] ?? []
    if (values.length === 0) continue
    fields.push({
      id: field,
      label: t(`catalogueBrowse.filters.${field}`),
      type: 'select',
      options: values.map((v) => ({
        value: v.valueCode,
        label: `${labelFor(listCode, v.valueCode)} (${v.count})`,
      })),
    })
  }

  for (const field of NUMERIC_FACET_FIELDS) {
    const range = facets?.numeric?.[field]
    // Offer the field even before facets load — a number input is always safe.
    if (facets && !range) continue
    fields.push({ id: field, label: t(`catalogueBrowse.filters.${field}`), type: 'number' })
  }

  return fields
}

/**
 * FilterPredicate[] → catalogue `/variants` query params. `/catalogue`
 * accepts one equality per coded field and `<field>Min` / `<field>Max`
 * per numeric field; a `select` "is not" and a numeric "equals" have no
 * backend form and are left unsent rather than silently misfiltering
 * (same posture as `CustomersListPage.applyPredicatesToParams`).
 */
export function applyCatalogueFilters(params: URLSearchParams, predicates: FilterPredicate[]): void {
  for (const p of predicates) {
    if (p.fieldId in FACET_LIST_CODE) {
      if (p.condition === 'is' && typeof p.value === 'string') params.set(p.fieldId, p.value)
      continue
    }
    if ((NUMERIC_FACET_FIELDS as readonly string[]).includes(p.fieldId) && typeof p.value === 'number') {
      if (p.condition === 'greaterThan') params.set(`${p.fieldId}Min`, String(p.value))
      else if (p.condition === 'lessThan') params.set(`${p.fieldId}Max`, String(p.value))
    }
  }
}
