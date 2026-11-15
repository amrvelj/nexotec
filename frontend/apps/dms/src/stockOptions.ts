import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import { filterFieldsFromGridSchema, listQueryFilter } from './shared/gridSchema'
import type {
  GridSchemaRead,
  GroupStockDealershipRead,
  LedgerCategory,
  StockItemCondition,
  StockLifecycleStatus,
  StockReservationState,
} from './api/types'

type Translate = (key: string) => string

export function translatedStockLifecycleLabel(t: Translate, status: StockLifecycleStatus): string {
  return t(`stockEnums.lifecycleStatus.${status}`)
}

export function translatedStockLifecycleOptions(t: Translate): { value: StockLifecycleStatus; label: string }[] {
  return [
    { value: 'pipeline', label: t('stockEnums.lifecycleStatus.pipeline') },
    { value: 'in_stock', label: t('stockEnums.lifecycleStatus.in_stock') },
    { value: 'storno_pending', label: t('stockEnums.lifecycleStatus.storno_pending') },
  ]
}

export function translatedStockConditionLabel(t: Translate, condition: StockItemCondition): string {
  return t(`stockEnums.condition.${condition}`)
}

export function translatedStockConditionOptions(t: Translate): { value: StockItemCondition; label: string }[] {
  return [
    { value: 'new', label: t('stockEnums.condition.new') },
    { value: 'used', label: t('stockEnums.condition.used') },
    { value: 'demo', label: t('stockEnums.condition.demo') },
    { value: 'tagesz', label: t('stockEnums.condition.tagesz') },
  ]
}

export function translatedStockReservationLabel(t: Translate, state: StockReservationState): string {
  return t(`stockEnums.reservationState.${state}`)
}

// einstandspreis/verkaufserloes/foerderung excluded — automatic-only
// server-side (app.inventory.models.stock_item_ledger.AUTOMATIC_ONLY_CATEGORIES;
// the purchase booking posts the Einstandspreis since KAN-146), so
// hand-booking them from RecordCostDialog is never offered.
const HAND_BOOKABLE_LEDGER_CATEGORIES: LedgerCategory[] = [
  'landed_cost', 'aufbereitung', 'reparatur', 'gutachten', 'standkosten',
  'werbung', 'garantie', 'abwertung', 'promotion', 'sonstige_kosten',
  'kickback', 'zusatzerloes', 'sonstige_ertraege',
]

export function translatedLedgerCategoryLabel(t: Translate, category: LedgerCategory): string {
  return t(`stockEnums.ledgerCategory.${category}`)
}

export function translatedLedgerCategoryOptions(t: Translate): { value: LedgerCategory; label: string }[] {
  return HAND_BOOKABLE_LEDGER_CATEGORIES.map((value) => ({ value, label: translatedLedgerCategoryLabel(t, value) }))
}

/** The Stock grid's filter fields: every field the endpoint's served grid
 * schema lists, with the conditions it applies (KAN-461). Empty until the
 * schema has arrived — a predicate from a link then waits, it is not dropped
 * (the page narrows only once it has the schema). */
export function buildStockFilterFields(t: Translate, schema: GridSchemaRead | undefined): FilterFieldDef[] {
  return schema ? filterFieldsFromGridSchema(schema, t) : []
}

/** FilterPredicate[] → `/inventory/stock-items`'s `filter` parameters, one per
 * predicate (KAN-461). Every field and condition `buildStockFilterFields`
 * offers is sent; `architecture/grid-filters-are-applied.test.ts` fails if
 * one is not. */
export function applyStockFilters(params: URLSearchParams, predicates: FilterPredicate[], fields: FilterFieldDef[]): void {
  for (const predicate of predicates) {
    const filter = listQueryFilter(predicate, fields)
    if (filter !== null) params.append('filter', filter)
  }
}

/** The group stock grid's filter fields (KAN-565, ADR-055 «the dealership as
 * a filter»): one select, «Betrieb», «ist» or «ist nicht» — the endpoint's
 * `dealershipId` / `dealershipIdNot`. Offered whether or not the options have
 * arrived yet, so a link's predicate is not dropped while they load. */
export function buildGroupStockFilterFields(t: Translate, dealerships: GroupStockDealershipRead[]): FilterFieldDef[] {
  return [
    {
      id: 'dealershipId',
      label: t('stockList.columns.dealership'),
      type: 'select',
      options: dealerships.map((d) => ({ value: d.id, label: d.label })),
      conditions: ['is', 'isNot'],
    },
  ]
}

/** FilterPredicate[] → `/inventory/groups/mine/stock-items` query params.
 * Every field and condition `buildGroupStockFilterFields` offers is sent;
 * `architecture/grid-filters-are-applied.test.ts` fails if one is not. */
export function applyGroupStockFilters(params: URLSearchParams, predicates: FilterPredicate[]): void {
  for (const predicate of predicates) {
    if (predicate.fieldId !== 'dealershipId' || typeof predicate.value !== 'string') {
      // Not a field this grid offers, or no value — `offeredPredicates` passes neither.
    } else if (predicate.condition === 'is') params.append('dealershipId', predicate.value)
    else if (predicate.condition === 'isNot') params.append('dealershipIdNot', predicate.value)
  }
}
