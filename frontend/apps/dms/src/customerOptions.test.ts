import { describe, expect, it } from 'vitest'
import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import { applyCustomerFilters, UNASSIGNED_ADVISOR } from './customerOptions'

const FIELDS: FilterFieldDef[] = [
  { id: 'updatedAt', label: 'Changed', type: 'date', param: 'updated_since', conditions: ['inTheLastDays', 'moreThanDaysAgo'] },
  { id: 'advisor', label: 'Advisor', type: 'select', param: 'advisorUserId', conditions: ['is'] },
]
// A Wednesday, local time (resolveRelativeDateRange works in local days).
const NOW = new Date(2026, 9, 21, 12, 0, 0)

function paramsFor(predicates: FilterPredicate[]): Record<string, string> {
  const params = new URLSearchParams()
  applyCustomerFilters(params, predicates, FIELDS, NOW)
  return Object.fromEntries(params)
}

describe('applyCustomerFilters (KAN-458)', () => {
  it('«more than N days ago» is the day count, for the server to resolve', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'updatedAt', type: 'date', condition: 'moreThanDaysAgo', days: 90 }])).toEqual({
      updated_more_than_days_ago: '90',
    })
  })

  it('two date predicates on one field are ANDed — the later lower bound, the older cut-off', () => {
    expect(
      paramsFor([
        { id: 'a', fieldId: 'updatedAt', type: 'date', condition: 'inTheLastDays', days: 30 },
        { id: 'b', fieldId: 'updatedAt', type: 'date', condition: 'inTheLastDays', days: 7 },
        { id: 'c', fieldId: 'updatedAt', type: 'date', condition: 'moreThanDaysAgo', days: 2 },
        { id: 'd', fieldId: 'updatedAt', type: 'date', condition: 'moreThanDaysAgo', days: 5 },
      ]),
    ).toEqual({ updated_since: '2026-10-14', updated_more_than_days_ago: '5' })
  })

  it('the unassigned advisor queue is the API\'s own flag', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'advisor', type: 'select', condition: 'is', value: UNASSIGNED_ADVISOR }])).toEqual({
      unassigned: 'true',
    })
  })
})
