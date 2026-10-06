import { describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import { STOCK_GROUP_COLUMN_IDS, buildStockGroupColumns } from './stockGroupColumns'

// § ADR-055 — asserted by name, not just "fewer columns than the tenant
// grid." Any of these appearing here would leak an entity-private
// commercial fact across the group boundary.
const FORBIDDEN_COLUMN_IDS = [
  'effectivePrice',
  'landedCost',
  'purchasePrice',
  'purchaseInvoiceRef',
  'supplierName',
  'notionalInputTaxApplicable',
  'notionalInputTaxRate',
  'notionalInputTaxAmount',
  'isInvoiceable',
  'margin',
  // WP-7 PR-9
  'basePrice',
  'valuationRefId',
  'valuationRefAmount',
  'valuationRefValuedAt',
  'valuationRefSource',
]

describe('stockGroupColumns', () => {
  it('never exposes an entity-private commercial field by name', () => {
    for (const forbidden of FORBIDDEN_COLUMN_IDS) {
      expect(STOCK_GROUP_COLUMN_IDS as readonly string[]).not.toContain(forbidden)
    }
  })

  it('uses listPrice, never effectivePrice, as its price column', () => {
    expect(STOCK_GROUP_COLUMN_IDS).toContain('listPrice')
    expect(STOCK_GROUP_COLUMN_IDS as readonly string[]).not.toContain('effectivePrice')
  })

  it('carries dealershipLabel — the one dimension unique to this projection', () => {
    expect(STOCK_GROUP_COLUMN_IDS).toContain('dealershipLabel')
  })
})

// KAN-152 — the group grid sorts server-side. Every column that offers a
// sort must name a field GET /v1/inventory/groups/mine/stock-items accepts
// (anything else is a 422 at runtime). This set mirrors the backend's
// STOCK_ITEM_SORT_FIELDS (app/inventory/api/stock_items.py), which
// tests/test_inventory_group_listing_api.py pins from the other side.
const SERVER_SORT_FIELDS = ['stockNumber', 'vin', 'updatedAt', 'createdAt']

describe('stockGroupColumns — server-side sort (KAN-152)', () => {
  const columns = buildStockGroupColumns(i18n.t.bind(i18n), 'de-CH')
  const sortFields = columns.map((c) => c.meta?.sortField).filter((f): f is string => Boolean(f))

  it('offers only sorts the group endpoint accepts', () => {
    expect(sortFields.filter((f) => !SERVER_SORT_FIELDS.includes(f))).toEqual([])
  })

  it('makes every sortable stock column sortable, like the own-stock grid', () => {
    expect([...sortFields].sort()).toEqual(['stockNumber', 'updatedAt', 'vin'])
  })
})
