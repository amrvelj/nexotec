import type { CatalogueBrowseMode, VehicleSpecBlockRead } from './api/types'

export type ConfiguratorMode = CatalogueBrowseMode // 'build' | 'record'

export function configuratorModeOptions(t: (k: string) => string): { value: ConfiguratorMode; label: string }[] {
  return [
    { value: 'build', label: t('configurator.mode.build') },
    { value: 'record', label: t('configurator.mode.record') },
  ]
}

export type SpecFieldKind = 'int' | 'decimal' | 'text' | 'ref'

export interface SpecFieldDef {
  /** key on `VehicleSpecBlockRead` (camelCase) */
  key: keyof VehicleSpecBlockRead
  kind: SpecFieldKind
  /** reference list code, for `kind: 'ref'` */
  listCode?: string
  /** i18n key under `configurator.spec.<key>` — falls back to
   * `catalogueBrowse.columns.<key>` where a label already exists */
}

/**
 * FR-C-05 — every field of the ADR-071 spec block is enterable by hand.
 * Grouped for the form; the grouping matches the model's own comment
 * blocks. `kind` drives which input renders.
 */
export const SPEC_FIELD_GROUPS: { titleKey: string; fields: SpecFieldDef[] }[] = [
  {
    titleKey: 'configurator.spec.groups.identity',
    fields: [
      { key: 'modelTypeName', kind: 'text' },
      { key: 'trimName', kind: 'text' },
      { key: 'modelYear', kind: 'int' },
      { key: 'productionFrom', kind: 'int' },
      { key: 'productionTo', kind: 'int' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.powertrain',
    fields: [
      { key: 'engineCycle', kind: 'ref', listCode: 'engine_cycle' },
      { key: 'displacementCcm', kind: 'int' },
      { key: 'cylinders', kind: 'int' },
      { key: 'gears', kind: 'int' },
      { key: 'ps', kind: 'int' },
      { key: 'kw', kind: 'int' },
      { key: 'totalPs', kind: 'int' },
      { key: 'totalKw', kind: 'int' },
      { key: 'systemKw', kind: 'int' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.classification',
    fields: [
      { key: 'vehicleClass', kind: 'ref', listCode: 'vehicle_class' },
      { key: 'valuationClassification', kind: 'ref', listCode: 'valuation_classification' },
      { key: 'emissionStandard', kind: 'ref', listCode: 'emission_standard' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.dimensions',
    fields: [
      { key: 'doors', kind: 'int' },
      { key: 'seats', kind: 'int' },
      { key: 'weightEmptyKg', kind: 'int' },
      { key: 'weightTotalKg', kind: 'int' },
      { key: 'payloadKg', kind: 'int' },
      { key: 'towingCapacityKg', kind: 'int' },
      { key: 'wheelbaseMm', kind: 'int' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.consumption',
    fields: [
      { key: 'consumptionMixed', kind: 'decimal' },
      { key: 'consumptionUrban', kind: 'decimal' },
      { key: 'consumptionExtraUrban', kind: 'decimal' },
      { key: 'consumptionNorm', kind: 'ref', listCode: 'consumption_norm' },
      { key: 'co2Gkm', kind: 'int' },
      { key: 'energyConsumptionKwh', kind: 'decimal' },
      { key: 'batteryCapacityKwh', kind: 'decimal' },
      { key: 'rangeKm', kind: 'int' },
      { key: 'tankCapacityL', kind: 'int' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.codes',
    fields: [
      { key: 'werkscode', kind: 'text' },
      { key: 'importcode', kind: 'text' },
    ],
  },
  {
    titleKey: 'configurator.spec.groups.price',
    fields: [
      { key: 'basePrice', kind: 'decimal' },
      { key: 'basePriceYear', kind: 'int' },
    ],
  },
]

export const ALL_SPEC_FIELDS: SpecFieldDef[] = SPEC_FIELD_GROUPS.flatMap((g) => g.fields)

export const SPEC_REF_LIST_CODES: string[] = Array.from(
  new Set(ALL_SPEC_FIELDS.filter((f) => f.kind === 'ref').map((f) => f.listCode!)),
)

/** i18n key for a spec field's label — reuse the catalogue-browse column
 * label where it exists, else `configurator.spec.fields.<key>`. */
const CATALOGUE_LABEL_KEYS = new Set([
  'trimName',
  'engineCycle',
  'displacementCcm',
  'cylinders',
  'gears',
  'ps',
  'kw',
  'vehicleClass',
  'emissionStandard',
  'doors',
  'seats',
  'co2Gkm',
  'basePrice',
  'modelYear',
  'productionFrom',
  'productionTo',
])

export function specFieldLabelKey(key: string): string {
  if (key === 'basePrice') return 'catalogueBrowse.columns.basePrice'
  if (CATALOGUE_LABEL_KEYS.has(key)) return `catalogueBrowse.columns.${key}`
  return `configurator.spec.fields.${key}`
}
