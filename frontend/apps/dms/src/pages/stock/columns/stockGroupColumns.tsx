import type { GridColumnDef } from '@nexotec/ui-kit'
import {
  StockConditionBadge,
  StockLifecycleBadge,
  StockReservationBadge,
} from '@nexotec/ui-kit'
import {
  translatedStockConditionLabel,
  translatedStockLifecycleLabel,
  translatedStockReservationLabel,
} from '../../../stockOptions'
import { formatCurrencyChf, formatDate } from '../../../utils/format'
import type { StockItemGroupRead } from '../../../api/types'

type Translate = (key: string) => string

/**
 * § ADR-055 — a deliberately SEPARATE, hand-authored column set, never
 * derived from the own-stock grid's columns. `stockGroupColumns.test.ts`
 * asserts this list by name against the forbidden set (effectivePrice,
 * landedCost, notionalInputTax*, purchasePrice, margin/Wagenbuch fields)
 * — not just "fewer columns than the tenant grid."
 */
export const STOCK_GROUP_COLUMN_IDS = [
  'stockNumber',
  'vehicleLabel',
  'dealershipLabel',
  'condition',
  'lifecycleStatus',
  'reservationState',
  'listPrice',
  'odometerKm',
  'firstRegistrationDate',
  'vin',
  'updatedAt',
] as const

export function buildStockGroupColumns(t: Translate, locale: string): GridColumnDef<StockItemGroupRead>[] {
  return [
    {
      id: 'stockNumber',
      header: t('stockList.columns.stockNumber'),
      cell: ({ row }) => row.original.stockNumber,
      meta: { sortField: 'stockNumber', pinned: 'left', mono: true, locked: true },
    },
    {
      id: 'vehicleLabel',
      header: t('stockList.columns.vehicle'),
      cell: ({ row }) => <span style={{ fontWeight: 600 }}>{row.original.vehicleLabel}</span>,
      meta: { locked: true },
    },
    {
      id: 'dealershipLabel',
      header: t('stockList.columns.dealership'),
      cell: ({ row }) => row.original.dealershipLabel,
      meta: { sortField: 'dealershipLabel' },
    },
    {
      id: 'condition',
      header: t('stockList.columns.condition'),
      cell: ({ row }) => (
        <StockConditionBadge condition={row.original.condition} label={translatedStockConditionLabel(t, row.original.condition)} />
      ),
    },
    {
      id: 'lifecycleStatus',
      header: t('stockList.columns.lifecycleStatus'),
      cell: ({ row }) => (
        <StockLifecycleBadge status={row.original.lifecycleStatus} label={translatedStockLifecycleLabel(t, row.original.lifecycleStatus)} />
      ),
      meta: { defaultVisible: false },
    },
    {
      id: 'reservationState',
      header: t('stockList.columns.reservationState'),
      cell: ({ row }) => (
        <StockReservationBadge state={row.original.reservationState} label={translatedStockReservationLabel(t, row.original.reservationState)} />
      ),
    },
    {
      id: 'listPrice',
      header: t('stockList.columns.listPrice'),
      cell: ({ row }) => (row.original.listPrice != null ? formatCurrencyChf(Number(row.original.listPrice)) : '—'),
      meta: { align: 'right' },
    },
    {
      id: 'odometerKm',
      header: t('stockList.columns.odometerKm'),
      cell: ({ row }) => (row.original.odometerKm != null ? row.original.odometerKm.toLocaleString(locale) : '—'),
      meta: { align: 'right', defaultVisible: false },
    },
    {
      id: 'firstRegistrationDate',
      header: t('stockDetail.spec.firstRegistrationDate'),
      cell: ({ row }) => (row.original.firstRegistrationDate ? formatDate(row.original.firstRegistrationDate, locale) : '—'),
      meta: { defaultVisible: false },
    },
    {
      id: 'vin',
      header: t('stockList.columns.vin'),
      cell: ({ row }) => row.original.vin ?? '—',
      meta: { defaultVisible: false, mono: true },
    },
    {
      id: 'updatedAt',
      header: t('stockList.columns.changed'),
      cell: ({ row }) => formatDate(row.original.updatedAt, locale),
      meta: { sortField: 'updatedAt', align: 'right' },
    },
  ]
}
