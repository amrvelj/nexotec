import type { StockItemCondition, StockLifecycleStatus, StockReservationState } from './api/types'

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
