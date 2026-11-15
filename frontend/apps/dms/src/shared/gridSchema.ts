import {
  ALL_CONDITIONS_BY_TYPE,
  type FilterFieldDef,
  type FilterFieldType,
  type FilterPredicate,
} from '@nexotec/ui-kit'
import type { GridField, GridSchemaRead } from '../api/types'

// KAN-461 — a grid on the shared list-query layer (app/core/list_query.py)
// derives its filter fields and sortable headers from the endpoint's served
// schema (`GET …/grid-schema`), the one source of truth (ADR-058, FR-UI-01):
// nothing here lists a field by hand.

type Translate = (key: string) => string

const KIT_TYPE: Record<GridField['type'], FilterFieldType> = {
  text: 'text',
  enum: 'select',
  number: 'number',
  money: 'number',
  date: 'date',
  datetime: 'date',
  boolean: 'boolean',
}

/** One filter field per served field, offering exactly the conditions the
 * endpoint applies that the builder can take (an enum's `in` is served, not
 * offered: the builder's select holds one value). */
export function filterFieldsFromGridSchema(schema: GridSchemaRead, t: Translate): FilterFieldDef[] {
  return schema.fields.map((field) => {
    const type = KIT_TYPE[field.type]
    const kitConditions = new Set<string>(ALL_CONDITIONS_BY_TYPE[type])
    return {
      id: field.field,
      label: t(field.labelKey),
      type,
      options: field.options?.map((o) => ({ value: o.value, label: t(o.labelKey) })),
      conditions: field.operators.filter((op) => kitConditions.has(op)),
      numberFormat: field.type === 'money' ? 'chf' : undefined,
    }
  })
}

/** The fields the endpoint sorts — `DataGrid`'s `sortableFields`. */
export function sortableFieldsOf(schema: GridSchemaRead | undefined): ReadonlySet<string> {
  return new Set((schema?.fields ?? []).filter((f) => f.sortable).map((f) => f.field))
}

function numberText(value: unknown, money: boolean): string {
  return typeof value === 'number' && Number.isFinite(value) ? (money ? value.toFixed(2) : String(value)) : ''
}

/** A predicate as the layer's `filter` parameter: `field:operator[:value]`
 * (money with two decimals, a range `low,high`, a day YYYY-MM-DD, a day
 * count, a boolean `true`/`false`). Null for a predicate without the value
 * its condition needs — `offeredPredicates` passes none such. */
export function listQueryFilter(predicate: FilterPredicate, fields: FilterFieldDef[]): string | null {
  const field = fields.find((f) => f.id === predicate.fieldId)
  if (!field) return null
  const head = `${predicate.fieldId}:${predicate.condition}`
  const money = field.numberFormat === 'chf'
  switch (predicate.type) {
    case 'boolean':
      return `${head}:${predicate.value === false ? 'false' : 'true'}`
    case 'number': {
      const value =
        predicate.condition === 'between'
          ? `${numberText(predicate.value, money)},${numberText(predicate.valueTo, money)}`
          : numberText(predicate.value, money)
      return value && !value.startsWith(',') && !value.endsWith(',') ? `${head}:${value}` : null
    }
    case 'date':
      if (predicate.condition === 'inTheLastDays' || predicate.condition === 'moreThanDaysAgo') {
        return typeof predicate.days === 'number' ? `${head}:${predicate.days}` : null
      }
      if (predicate.condition === 'on' || predicate.condition === 'before' || predicate.condition === 'after') {
        return typeof predicate.value === 'string' && predicate.value ? `${head}:${predicate.value}` : null
      }
      return head
    default:
      return typeof predicate.value === 'string' && predicate.value !== '' ? `${head}:${predicate.value}` : null
  }
}
