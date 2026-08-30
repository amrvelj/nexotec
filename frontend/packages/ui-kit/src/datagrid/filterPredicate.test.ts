import { describe, expect, it } from 'vitest'
import { describePredicate, resolveRelativeDateRange, type FilterFieldDef, type FilterPredicate } from './filterPredicate'

// Constructed in LOCAL time (month is 0-indexed: 2 = March), matching
// resolveRelativeDateRange's own local-calendar semantics
// (getFullYear/getMonth/getDate/setHours are all local, not UTC) — an ISO
// UTC string here would drift the local calendar date near either edge of
// a day depending on the test runner's own timezone. A Wednesday, so
// "this week" has days on both sides of it to check.
const WEDNESDAY = new Date(2026, 2, 11, 12, 0, 0)

describe('resolveRelativeDateRange', () => {
  it('today resolves to a single-day range', () => {
    expect(resolveRelativeDateRange('today', undefined, WEDNESDAY)).toEqual({ from: '2026-03-11', to: '2026-03-11' })
  })

  it('thisWeek resolves Monday to Sunday, ISO/Swiss convention', () => {
    expect(resolveRelativeDateRange('thisWeek', undefined, WEDNESDAY)).toEqual({ from: '2026-03-09', to: '2026-03-15' })
  })

  it('thisWeek anchored on a Sunday still resolves back to the Monday that started it', () => {
    const sunday = new Date(2026, 2, 15, 12, 0, 0)
    expect(resolveRelativeDateRange('thisWeek', undefined, sunday)).toEqual({ from: '2026-03-09', to: '2026-03-15' })
  })

  it('thisMonth resolves the full calendar month', () => {
    expect(resolveRelativeDateRange('thisMonth', undefined, WEDNESDAY)).toEqual({ from: '2026-03-01', to: '2026-03-31' })
  })

  it('thisYear resolves the full calendar year', () => {
    expect(resolveRelativeDateRange('thisYear', undefined, WEDNESDAY)).toEqual({ from: '2026-01-01', to: '2026-12-31' })
  })

  it('inTheLastDays counts back from today, inclusive of today', () => {
    expect(resolveRelativeDateRange('inTheLastDays', 7, WEDNESDAY)).toEqual({ from: '2026-03-04', to: '2026-03-11' })
  })

  it('moreThanDaysAgo has no lower bound and excludes the boundary day itself (exactly 30 days ago is not "more than" 30)', () => {
    // Today (Mar 11) minus 30 days is Feb 9 — exactly 30 days old, so the
    // upper bound is the day before it, Feb 8.
    expect(resolveRelativeDateRange('moreThanDaysAgo', 30, WEDNESDAY)).toEqual({ from: null, to: '2026-02-08' })
  })

  it('re-evaluates to a different range on a different day, since it never stores an absolute date', () => {
    const laterInTheYear = new Date(2026, 5, 1, 12, 0, 0)
    expect(resolveRelativeDateRange('today', undefined, WEDNESDAY)).not.toEqual(
      resolveRelativeDateRange('today', undefined, laterInTheYear)
    )
  })
})

describe('describePredicate', () => {
  const cantonField: FilterFieldDef = {
    id: 'canton',
    label: 'Canton',
    type: 'select',
    options: [{ value: 'ZH', label: 'Zürich' }, { value: 'GE', label: 'Geneva' }],
  }
  const changedField: FilterFieldDef = { id: 'updatedAt', label: 'Changed', type: 'date' }
  const consentField: FilterFieldDef = { id: 'marketingConsent', label: 'Marketing consent', type: 'boolean' }

  it('describes a select predicate using the option label, not the raw value', () => {
    const predicate: FilterPredicate = { id: '1', fieldId: 'canton', type: 'select', condition: 'is', value: 'ZH' }
    expect(describePredicate(predicate, cantonField)).toBe('Canton is Zürich')
  })

  it('describes a relative date predicate with its day count substituted in', () => {
    const predicate: FilterPredicate = { id: '2', fieldId: 'updatedAt', type: 'date', condition: 'inTheLastDays', days: 30 }
    expect(describePredicate(predicate, changedField)).toBe('Changed is in the last 30 days')
  })

  it('describes a boolean predicate without repeating the field label awkwardly', () => {
    const truePredicate: FilterPredicate = { id: '3', fieldId: 'marketingConsent', type: 'boolean', condition: 'is', value: true }
    const falsePredicate: FilterPredicate = { id: '4', fieldId: 'marketingConsent', type: 'boolean', condition: 'is', value: false }
    expect(describePredicate(truePredicate, consentField)).toBe('Marketing consent')
    expect(describePredicate(falsePredicate, consentField)).toBe('Not Marketing consent')
  })
})
