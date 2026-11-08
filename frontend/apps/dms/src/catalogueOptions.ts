import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type { CatalogueBrowseMode, CatalogueFacetsRead } from './api/types'

/** Mode → the production-year default (PRD `NurNeue`): `build` lists the
 * model groups in production, `record` everything. */
export function catalogueModeOptions(t: (k: string) => string): { value: CatalogueBrowseMode; label: string }[] {
  return [
    { value: 'build', label: t('catalogueBrowse.mode.build') },
    { value: 'record', label: t('catalogueBrowse.mode.record') },
  ]
}

/** Facet field (camelCase, as the API returns it) → the canonical
 * reference list its value codes come from, so the grid can show
 * translated labels instead of raw codes. Only fields the identAPI filters
 * (ADR-112, KAN-548): the vehicle kind is the drill-down's scope, not a
 * filter; `engineCycle` has no provider filter field. */
export const FACET_LIST_CODE: Record<string, string> = {
  fuelType: 'fuel_type',
  bodyStyle: 'body_style',
  drivetrain: 'drivetrain',
  transmission: 'transmission',
  vehicleClass: 'vehicle_class',
  emissionStandard: 'emission_standard',
}

/** The reference lists the grid shows labels from: the facet lists and the
 * vehicle kind (the drill-down's first choice). */
export const CATALOGUE_LABEL_LISTS: string[] = Array.from(new Set([...Object.values(FACET_LIST_CODE), 'vehicle_kind']))

/** The numbers the identAPI filters on (its OpenAPI document marks `ps`,
 * the list price and the production years «Not Filterable»). */
const NUMERIC_FACET_FIELDS = ['kw', 'displacementCcm', 'doors', 'seats', 'co2Gkm'] as const

/** The conditions `/catalogue/variants` honours exactly (KAN-458): one
 * coded value per field («ist»; the identAPI has no «ist nicht»), and the
 * inclusive `…Min`/`…Max` bounds read as «at least» / «at most»; «equals»
 * is both bounds at once. */
const CODED_CONDITIONS = ['is']
const NUMERIC_CONDITIONS = ['equals', 'atLeast', 'atMost']

/** Every field the catalogue grid can filter on, whatever the current
 * facets hold — what a predicate from the URL or a saved view is checked
 * against (`offeredPredicates`), so one is not dropped while the facets
 * are still loading. Labels are not shown from here. */
export const CATALOGUE_FILTER_SCOPE: FilterFieldDef[] = [
  ...Object.keys(FACET_LIST_CODE).map((id): FilterFieldDef => ({ id, label: id, type: 'select', conditions: CODED_CONDITIONS })),
  ...NUMERIC_FACET_FIELDS.map((id): FilterFieldDef => ({ id, label: id, type: 'number', conditions: NUMERIC_CONDITIONS })),
]

/**
 * The FilterBuilder field list. A coded field offers the values the
 * provider holds for the brand in view (`/catalogue/facets`, one request per
 * field — ADR-112), so a user never picks an option that returns nothing
 * (PRD FR-C-01); before a brand is chosen, or while the facets load, no
 * coded field is offered. The numbers are typed freely — the identAPI
 * carries no min/max — and are offered always.
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
      options: values.map((v) => ({ value: v.valueCode, label: labelFor(listCode, v.valueCode) })),
      conditions: CODED_CONDITIONS,
    })
  }

  for (const field of NUMERIC_FACET_FIELDS) {
    fields.push({
      id: field,
      label: t(`catalogueBrowse.filters.${field}`),
      type: 'number',
      // KAN-421: a saved filter reads its value as the grid shows it — every
      // figure here through the Swiss formatter (no CHF, no year: neither is
      // a provider filter).
      numberFormat: 'grouped',
      conditions: NUMERIC_CONDITIONS,
    })
  }

  return fields
}

/**
 * FilterPredicate[] → catalogue `/variants` query params. `/catalogue`
 * accepts one equality per coded field and `<field>Min` / `<field>Max` per
 * numeric field, both inclusive. Every field and condition
 * `buildCatalogueFilterFields` offers is sent;
 * `architecture/grid-filters-are-applied.test.ts` fails if one is not
 * (KAN-458).
 */
export function applyCatalogueFilters(params: URLSearchParams, predicates: FilterPredicate[]): void {
  for (const p of predicates) {
    if (p.fieldId in FACET_LIST_CODE) {
      if (p.condition === 'is' && typeof p.value === 'string') params.set(p.fieldId, p.value)
    } else if ((NUMERIC_FACET_FIELDS as readonly string[]).includes(p.fieldId) && typeof p.value === 'number') {
      const value = String(p.value)
      if (p.condition === 'atLeast' || p.condition === 'equals') params.set(`${p.fieldId}Min`, value)
      if (p.condition === 'atMost' || p.condition === 'equals') params.set(`${p.fieldId}Max`, value)
    }
  }
}

/** A catalogue number predicate saved before KAN-458 says «grösser als» /
 * «kleiner als», which the grid always sent as the inclusive `…Min` /
 * `…Max`. Read as «mindestens» / «höchstens» it keeps the effect it had and
 * now says so; every other predicate is returned as it was. */
export function readLegacyCataloguePredicates(predicates: FilterPredicate[]): FilterPredicate[] {
  return predicates.map((p) => {
    if (p.type !== 'number') return p
    if (p.condition === 'greaterThan') return { ...p, condition: 'atLeast' }
    if (p.condition === 'lessThan') return { ...p, condition: 'atMost' }
    return p
  })
}
