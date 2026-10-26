import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type { CatalogueBrowseMode, CatalogueFacetsRead } from './api/types'
import { formatNumber } from './utils/format'
import { setLowerBound, setUpperBound } from './utils/filterParams'

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

/** The conditions `/catalogue/variants` honours exactly (KAN-458): one
 * coded value per field — no «is not» — and the inclusive `…Min`/`…Max`
 * bounds, read as «at least» / «at most»; «equals» is both bounds at once.
 * A strict «greater than» would be answered with ≥ — never offered. */
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
        label: `${labelFor(listCode, v.valueCode)} (${formatNumber(v.count)})`,
      })),
      conditions: CODED_CONDITIONS,
    })
  }

  for (const field of NUMERIC_FACET_FIELDS) {
    const range = facets?.numeric?.[field]
    // Offer the field even before facets load — a number input is always safe.
    if (facets && !range) continue
    fields.push({
      id: field,
      label: t(`catalogueBrowse.filters.${field}`),
      type: 'number',
      // KAN-421: a saved filter reads its value as the grid shows it — the
      // list price in CHF (ADR-076), a model year ungrouped, every other
      // figure through the Swiss formatter.
      numberFormat: field === 'basePrice' ? 'chf' : field === 'modelYearFrom' ? 'ungrouped' : 'grouped',
      conditions: NUMERIC_CONDITIONS,
    })
  }

  return fields
}

/**
 * FilterPredicate[] → catalogue `/variants` query params. `/catalogue`
 * accepts one equality per coded field and `<field>Min` / `<field>Max`
 * per numeric field, both inclusive. Every field and condition
 * `buildCatalogueFilterFields` offers is sent;
 * `architecture/grid-filters-are-applied.test.ts` fails if one is not
 * (KAN-458).
 */
export function applyCatalogueFilters(params: URLSearchParams, predicates: FilterPredicate[]): void {
  for (const p of predicates) {
    if (p.fieldId in FACET_LIST_CODE) {
      if (p.condition === 'is' && typeof p.value === 'string') params.set(p.fieldId, p.value)
    } else if ((NUMERIC_FACET_FIELDS as readonly string[]).includes(p.fieldId) && typeof p.value === 'number') {
      // Two bounds on one field are ANDed: «Türen = 5» and «mindestens 3»
      // stay doorsMin=5&doorsMax=5, never widen to 3-door cars.
      if (p.condition === 'atLeast' || p.condition === 'equals') setLowerBound(params, `${p.fieldId}Min`, p.value)
      if (p.condition === 'atMost' || p.condition === 'equals') setUpperBound(params, `${p.fieldId}Max`, p.value)
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
