import { describe, expect, it } from 'vitest'
import type { FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import { applyCustomerFilters, UNASSIGNED_ADVISOR } from './customerOptions'

const FIELDS: FilterFieldDef[] = [
  {
    id: 'updatedAt',
    label: 'Changed',
    type: 'date',
    param: 'updated_since',
    conditions: ['today', 'thisWeek', 'thisMonth', 'thisYear', 'inTheLastDays', 'moreThanDaysAgo'],
  },
  { id: 'advisor', label: 'Advisor', type: 'select', param: 'advisorUserId', conditions: ['is', 'isNot'] },
  { id: 'language', label: 'Language', type: 'select', param: 'language', conditions: ['is', 'isNot'] },
]
function paramsFor(predicates: FilterPredicate[]): Record<string, string> {
  const params = new URLSearchParams()
  applyCustomerFilters(params, predicates, FIELDS)
  return Object.fromEntries(params)
}

describe('applyCustomerFilters (KAN-458)', () => {
  it('«more than N days ago» is the day count, for the server to resolve', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'updatedAt', type: 'date', condition: 'moreThanDaysAgo', days: 90 }])).toEqual({
      updated_more_than_days_ago: '90',
    })
  })

  // KAN-474 — every relative condition is sent as a condition (+ N), never a
  // date the browser resolved: `updated_since=2026-10-28` was bound as UTC
  // midnight, one to two hours after the Zurich midnight the server uses.
  it.each([
    ['today', 'day'],
    ['thisWeek', 'week'],
    ['thisMonth', 'month'],
    ['thisYear', 'year'],
  ] as const)('«%s» is the period, for the server to resolve', (condition, period) => {
    expect(paramsFor([{ id: 'a', fieldId: 'updatedAt', type: 'date', condition }])).toEqual({ updated_since_start_of: period })
  })

  it('«in the last N days» is the day count, never a browser-resolved date', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'updatedAt', type: 'date', condition: 'inTheLastDays', days: 30 }])).toEqual({
      updated_in_last_days: '30',
    })
  })

  it('no relative condition sends updated_since, the incremental-sync bound', () => {
    for (const condition of ['today', 'thisWeek', 'thisMonth', 'thisYear', 'inTheLastDays', 'moreThanDaysAgo'] as const) {
      expect(paramsFor([{ id: 'a', fieldId: 'updatedAt', type: 'date', condition, days: 7 }])).not.toHaveProperty('updated_since')
    }
  })

  it('the unassigned advisor queue is the API\'s own flag', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'advisor', type: 'select', condition: 'is', value: UNASSIGNED_ADVISOR }])).toEqual({
      unassigned: 'true',
    })
  })
})

describe('applyCustomerFilters — «ist nicht» (KAN-476)', () => {
  it('sends each select field\'s exclusion under the endpoint\'s own name', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'language', type: 'select', condition: 'isNot', value: 'de' }])).toEqual({ language_not: 'de' })
    expect(paramsFor([{ id: 'a', fieldId: 'advisor', type: 'select', condition: 'isNot', value: 'user-7' }])).toEqual({
      advisorUserIdNot: 'user-7',
    })
  })

  it('«ist nicht Nicht zugewiesen» asks for the customers that have an advisor', () => {
    expect(paramsFor([{ id: 'a', fieldId: 'advisor', type: 'select', condition: 'isNot', value: UNASSIGNED_ADVISOR }])).toEqual({
      assigned: 'true',
    })
  })
})
