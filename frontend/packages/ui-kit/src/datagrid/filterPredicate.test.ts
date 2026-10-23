import { describe, expect, it } from 'vitest'
import {
  describePredicate,
  resolveRelativeDateRange,
  type FilterFieldDef,
  type FilterPredicate,
  type PredicateTextParts,
} from './filterPredicate'
import { FR_CONDITION_LABELS } from '../test/frenchLabels'

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
  // KAN-415 — the text is the caller's translated template for the
  // condition, never field + English condition + value glued together.
  const cantonField: FilterFieldDef = {
    id: 'canton',
    label: 'Canton',
    type: 'select',
    options: [{ value: 'ZH', label: 'Zurich' }, { value: 'GE', label: 'Genève' }],
  }
  const nameField: FilterFieldDef = { id: 'name', label: 'Nom', type: 'text' }
  const priceField: FilterFieldDef = { id: 'price', label: 'Prix', type: 'number' }
  const changedField: FilterFieldDef = { id: 'updatedAt', label: 'Modifié', type: 'date' }
  const consentField: FilterFieldDef = { id: 'marketingConsent', label: 'Consentement marketing', type: 'boolean' }

  it('describes a select predicate using the option label, not the raw value', () => {
    const predicate: FilterPredicate = { id: '1', fieldId: 'canton', type: 'select', condition: 'is', value: 'ZH' }
    expect(describePredicate(predicate, cantonField, FR_CONDITION_LABELS)).toBe('Canton est Zurich')
  })

  it('describes text and number predicates through their own condition template', () => {
    const contains: FilterPredicate = { id: '2', fieldId: 'name', type: 'text', condition: 'contains', value: 'Muller' }
    const greater: FilterPredicate = { id: '3', fieldId: 'price', type: 'number', condition: 'greaterThan', value: 40000 }
    expect(describePredicate(contains, nameField, FR_CONDITION_LABELS)).toBe('Nom contient Muller')
    expect(describePredicate(greater, priceField, FR_CONDITION_LABELS)).toBe('Prix supérieur à 40000')
  })

  it('hands the day count to the N-days template', () => {
    const last: FilterPredicate = { id: '4', fieldId: 'updatedAt', type: 'date', condition: 'inTheLastDays', days: 30 }
    const older: FilterPredicate = { id: '5', fieldId: 'updatedAt', type: 'date', condition: 'moreThanDaysAgo', days: 7 }
    expect(describePredicate(last, changedField, FR_CONDITION_LABELS)).toBe('Modifié : au cours des 30 derniers jours')
    expect(describePredicate(older, changedField, FR_CONDITION_LABELS)).toBe('Modifié : il y a plus de 7 jours')
  })

  it('lets the template set the word order', () => {
    const labels = {
      ...FR_CONDITION_LABELS,
      predicateText: { ...FR_CONDITION_LABELS.predicateText, contains: ({ field, value }: PredicateTextParts) => `«${value}» in ${field}` },
    }
    const predicate: FilterPredicate = { id: '6', fieldId: 'name', type: 'text', condition: 'contains', value: 'ZH' }
    expect(describePredicate(predicate, nameField, labels)).toBe('«ZH» in Nom')
  })

  it('describes a boolean predicate with the true and false templates', () => {
    const truePredicate: FilterPredicate = { id: '7', fieldId: 'marketingConsent', type: 'boolean', condition: 'is', value: true }
    const falsePredicate: FilterPredicate = { id: '8', fieldId: 'marketingConsent', type: 'boolean', condition: 'is', value: false }
    expect(describePredicate(truePredicate, consentField, FR_CONDITION_LABELS)).toBe('Consentement marketing : oui')
    expect(describePredicate(falsePredicate, consentField, FR_CONDITION_LABELS)).toBe('Consentement marketing : non')
  })

  it('reads a condition its type does not know (a stale saved view) as the field label, not a raw value', () => {
    const stale: FilterPredicate = { id: '9', fieldId: 'name', type: 'text', condition: 'startsWith', value: 'A' }
    expect(describePredicate(stale, nameField, FR_CONDITION_LABELS)).toBe('Nom')
  })
})
