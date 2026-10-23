// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { describePredicate, type FilterFieldDef, type FilterPredicate } from '@nexotec/ui-kit'
import i18n from '../i18n'
import { useViewsAndFiltersLabels } from './useViewsAndFiltersLabels'

// KAN-415 — a saved filter's text comes from the bundles, one template per
// condition. Hardcoded expectations, not i18n.t(): a wrong or missing key must
// fail here. The day count goes through the Swiss number formatter (KAN-160)
// and picks the plural form.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const DOORS: FilterFieldDef = { id: 'doors', label: 'Türen', type: 'number' }
const CHANGED: FilterFieldDef = { id: 'updatedAt', label: 'Geändert', type: 'date' }
const CONSENT: FilterFieldDef = { id: 'consent', label: 'Einwilligung', type: 'boolean' }

function text(predicate: Omit<FilterPredicate, 'id' | 'fieldId'>, field: FilterFieldDef): string {
  const { result } = renderHook(() => useViewsAndFiltersLabels())
  return describePredicate({ id: 'p', fieldId: field.id, ...predicate }, field, result.current.filters)
}

describe('useViewsAndFiltersLabels: saved-filter text from the bundles (KAN-415)', () => {
  it('German: a number comparison reads whatever the field label\'s number or gender', async () => {
    await i18n.changeLanguage('de')
    expect(text({ type: 'number', condition: 'greaterThan', value: 3 }, DOORS)).toBe('Türen: mehr als 3')
    expect(text({ type: 'number', condition: 'equals', value: 5 }, DOORS)).toBe('Türen: genau 5')
  })

  it('German: the N-days texts pick the plural from the count and format it the Swiss way', async () => {
    await i18n.changeLanguage('de')
    expect(text({ type: 'date', condition: 'inTheLastDays', days: 1 }, CHANGED)).toBe(
      'Geändert: innerhalb des letzten Tages'
    )
    expect(text({ type: 'date', condition: 'inTheLastDays', days: 1500 }, CHANGED)).toBe(
      "Geändert: in den letzten 1'500 Tagen"
    )
    expect(text({ type: 'date', condition: 'moreThanDaysAgo', days: 30 }, CHANGED)).toBe(
      'Geändert: vor mehr als 30 Tagen'
    )
  })

  it('French, Italian and English: the day count is Swiss-formatted in every language', async () => {
    await i18n.changeLanguage('fr')
    expect(text({ type: 'date', condition: 'inTheLastDays', days: 1500 }, CHANGED)).toBe(
      "Geändert : au cours des 1'500 derniers jours"
    )
    await i18n.changeLanguage('it')
    expect(text({ type: 'date', condition: 'moreThanDaysAgo', days: 1500 }, CHANGED)).toBe(
      "Geändert: più di 1'500 giorni fa"
    )
    await i18n.changeLanguage('en')
    expect(text({ type: 'date', condition: 'inTheLastDays', days: 1 }, CHANGED)).toBe('Geändert: in the last day')
  })

  it('a boolean field reads as yes or no in the UI language', async () => {
    await i18n.changeLanguage('it')
    expect(text({ type: 'boolean', condition: 'is', value: true }, CONSENT)).toBe('Einwilligung: sì')
    expect(text({ type: 'boolean', condition: 'is', value: false }, CONSENT)).toBe('Einwilligung: no')
  })

  it('every condition has a name in every language', async () => {
    for (const lng of ['de', 'fr', 'it', 'en']) {
      await i18n.changeLanguage(lng)
      const { result } = renderHook(() => useViewsAndFiltersLabels())
      for (const name of Object.values(result.current.filters.conditions)) {
        expect(name).not.toMatch(/MISSING|common\.filterBuilder/)
      }
    }
  })
})
