import { describe, expect, it } from 'vitest'
import type { FilterPredicate } from '@nexotec/ui-kit'
import { applyCatalogueFilters, readLegacyCataloguePredicates } from './catalogueOptions'

// KAN-458: `/catalogue/variants` bounds are inclusive (`kwMin` is ≥), so the
// grid offers «at least» / «at most» and sends «equals» as both bounds.
function paramsFor(predicate: Omit<FilterPredicate, 'id'>): Record<string, string> {
  const params = new URLSearchParams()
  applyCatalogueFilters(params, [{ id: 'p', ...predicate }])
  return Object.fromEntries(params)
}

describe('applyCatalogueFilters', () => {
  it('«kW at least 110» is kwMin=110 — 110 kW included, as the label says', () => {
    expect(paramsFor({ fieldId: 'kw', type: 'number', condition: 'atLeast', value: 110 })).toEqual({ kwMin: '110' })
  })

  it('«CO₂ at most 120» is co2GkmMax=120; a field the identAPI does not filter (PS, price) is not sent (KAN-548)', () => {
    expect(paramsFor({ fieldId: 'ps', type: 'number', condition: 'atLeast', value: 150 })).toEqual({})
    expect(paramsFor({ fieldId: 'basePrice', type: 'number', condition: 'atMost', value: 40000 })).toEqual({})
    expect(paramsFor({ fieldId: 'co2Gkm', type: 'number', condition: 'atMost', value: 120 })).toEqual({
      co2GkmMax: '120',
    })
  })

  it('«doors equals 5» is both bounds at 5', () => {
    expect(paramsFor({ fieldId: 'doors', type: 'number', condition: 'equals', value: 5 })).toEqual({
      doorsMin: '5',
      doorsMax: '5',
    })
  })

  it('a coded field is one equality value', () => {
    expect(paramsFor({ fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' })).toEqual({
      fuelType: 'petrol',
    })
  })
})

describe('readLegacyCataloguePredicates', () => {
  it('reads a saved «grösser als» / «kleiner als» as the inclusive bound it was always sent as', () => {
    const read = readLegacyCataloguePredicates([
      { id: 'a', fieldId: 'kw', type: 'number', condition: 'greaterThan', value: 110 },
      { id: 'b', fieldId: 'kw', type: 'number', condition: 'lessThan', value: 220 },
      { id: 'c', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' },
    ])
    expect(read.map((p) => p.condition)).toEqual(['atLeast', 'atMost', 'is'])
  })
})
