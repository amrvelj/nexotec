import { describe, expect, it } from 'vitest'
import { STOCK_GROUP_COLUMN_IDS } from './stockGroupColumns'

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
