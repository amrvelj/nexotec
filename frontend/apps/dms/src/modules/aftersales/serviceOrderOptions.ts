import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type { LabourTimeUnit, ServiceOrderState } from '../../api/types'
import { formatNumber } from '../../utils/format'

type Translate = (key: string, options?: Record<string, unknown>) => string

/** The order page's route (App.tsx). */
export const serviceOrderHref = (id: string) => `/aftersales/orders/${id}`

/** Table 3.1's states, in the order an order passes them (KAN-810). */
export const SERVICE_ORDER_STATES: ServiceOrderState[] = ['draft', 'accepted', 'ready_for_billing', 'cancelled']

/** The states an order can still be changed in (table 3.1: «ready for billing» and «cancelled» are frozen).
 * A mirror of the server's `refuse_frozen`, for disabling an action with its reason — the server decides. */
export const OPEN_STATES: ReadonlySet<ServiceOrderState> = new Set(['draft', 'accepted'])

export function translatedStateOptions(t: Translate): { value: string; label: string }[] {
  return SERVICE_ORDER_STATES.map((state) => ({ value: state, label: t(`serviceOrder.state.${state}`) }))
}

/**
 * The order list's filters as `GET /v1/service-orders` takes them (KAN-458:
 * every offered predicate is sent; `architecture/grid-filters-are-applied.test.ts`
 * runs this). The list offers one filter, the state, with «ist» only: the
 * endpoint filters on one state and has no exclusion. The page passes only
 * offered predicates (`offeredPredicates`).
 */
export function applyServiceOrderFilters(params: URLSearchParams, predicates: FilterPredicate[], fields: FilterFieldDef[]): void {
  const fieldById = new Map(fields.map((f) => [f.id, f]))
  for (const predicate of predicates) {
    const field = fieldById.get(predicate.fieldId)
    const paramName = field ? (field.param ?? field.id) : undefined
    if (paramName !== undefined && predicate.condition === 'is' && typeof predicate.value === 'string') {
      params.set(paramName, predicate.value)
    }
  }
}

/** A decimal string from the API as a number for display, never for a calculation the server owns. */
export function decimal(value: string | number | null | undefined): number {
  return value === null || value === undefined || value === '' ? 0 : Number(value)
}

/** A labour time as typed, in the unit it was typed in (check 47: «9 AW», «0.75 h»). */
export function formatLabourTime(t: Translate, quantity: string, unit: LabourTimeUnit): string {
  return `${formatNumber(Number(quantity))} ${t(`serviceOrder.timeUnit.${unit}`)}`
}

/** A VAT rate as the API sends it (`8.10`) for a line label: `8.1`. */
export function formatVatRate(rate: string): string {
  return formatNumber(Number(rate))
}
