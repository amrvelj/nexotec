import { describe, expect, it } from 'vitest'
import type { FilterPredicate } from '@nexotec/ui-kit'
import { applyCatalogueFilters, readLegacyCataloguePredicates } from './catalogueOptions'

// KAN-458: `/catalogue/variants` bounds are inclusive (`psMin` is ≥), so the
// grid offers «at least» / «at most» and sends «equals» as both bounds.
function paramsFor(predicate: Omit<FilterPredicate, 'id'>): Record<string, string> {
  const params = new URLSearchParams()
  applyCatalogueFilters(params, [{ id: 'p', ...predicate }])
  return Object.fromEntries(params)
}

describe('applyCatalogueFilters', () => {
  it('«PS at least 150» is psMin=150 — 150 PS included, as the label says', () => {
    expect(paramsFor({ fieldId: 'ps', type: 'number', condition: 'atLeast', value: 150 })).toEqual({ psMin: '150' })
  })

  it('«price at most 40 000» is basePriceMax=40000', () => {
    expect(paramsFor({ fieldId: 'basePrice', type: 'number', condition: 'atMost', value: 40000 })).toEqual({
      basePriceMax: '40000',
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
      { id: 'a', fieldId: 'ps', type: 'number', condition: 'greaterThan', value: 150 },
      { id: 'b', fieldId: 'ps', type: 'number', condition: 'lessThan', value: 300 },
      { id: 'c', fieldId: 'fuelType', type: 'select', condition: 'is', value: 'petrol' },
    ])
    expect(read.map((p) => p.condition)).toEqual(['atLeast', 'atMost', 'is'])
  })
})
