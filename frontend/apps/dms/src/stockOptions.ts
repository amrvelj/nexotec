import type { LedgerCategory, StockItemCondition, StockLifecycleStatus, StockReservationState } from './api/types'

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

// verkaufserloes/foerderung excluded — automatic-only server-side
// (app.inventory.models.stock_item_ledger.AUTOMATIC_ONLY_CATEGORIES), so
// hand-booking them from RecordCostDialog is never offered.
const HAND_BOOKABLE_LEDGER_CATEGORIES: LedgerCategory[] = [
  'einstandspreis', 'landed_cost', 'aufbereitung', 'reparatur', 'gutachten', 'standkosten',
  'werbung', 'garantie', 'abwertung', 'promotion', 'sonstige_kosten',
  'kickback', 'zusatzerloes', 'sonstige_ertraege',
]

export function translatedLedgerCategoryLabel(t: Translate, category: LedgerCategory): string {
  return t(`stockEnums.ledgerCategory.${category}`)
}

export function translatedLedgerCategoryOptions(t: Translate): { value: LedgerCategory; label: string }[] {
  return HAND_BOOKABLE_LEDGER_CATEGORIES.map((value) => ({ value, label: translatedLedgerCategoryLabel(t, value) }))
}
