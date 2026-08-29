import { describe, expect, it } from 'vitest'
import { formatCurrencyChf, formatDate, formatDateTime, formatNumber } from './format'

// First real test in this workspace — establishes the vitest lane (WP-1,
// Gap G-14b). Exercises FR-13's fixed Swiss dd.MM.yyyy convention, which is
// exactly the kind of formatting rule a refactor could silently break
// without a test noticing.
// 12:00 UTC, not midnight — stays on the same calendar date in every
// real-world timezone offset (-12..+14), so this test doesn't depend on
// the timezone the test runner happens to be in.
const NOON_UTC = '2026-03-05T12:00:00Z'

describe('formatDate', () => {
  it('renders dd.MM.yyyy for the default de-CH locale', () => {
    expect(formatDate(NOON_UTC)).toBe('05.03.2026')
  })

  it('respects an explicit locale tag', () => {
    expect(formatDate(NOON_UTC, 'en-CH')).toBe('05.03.2026')
  })
})

describe('formatDateTime', () => {
  it('appends hour:minute after the dd.MM.yyyy date', () => {
    expect(formatDateTime(NOON_UTC)).toMatch(/^05\.03\.2026, \d{2}:\d{2}$/)
  })
})

describe('formatNumber', () => {
  it('groups thousands with the ASCII apostrophe (U+0027), not a typographic one', () => {
    const result = formatNumber(12482)
    expect(result).toBe("12'482")
    expect(result).not.toContain('’') // the typographic apostrophe Intl can emit elsewhere
  })

  it('always uses a period decimal separator, matching every PDF this app renders', () => {
    // fr-CH's own Intl data uses a comma here — this function pins to
    // de-CH internally specifically so a French UI user sees the same
    // decimal mark as the WeasyPrint-rendered document for the same figure.
    expect(formatNumber(1234.5)).toBe("1'234.5")
  })
})

describe('formatCurrencyChf', () => {
  it('renders two decimals with the CHF prefix and apostrophe grouping', () => {
    expect(formatCurrencyChf(12500)).toBe("CHF 12'500.00")
  })

  it('rounds to the nearest centime', () => {
    expect(formatCurrencyChf(12500.005)).toBe("CHF 12'500.01")
  })

  it('uses the real minus sign (U+2212), not a hyphen, for a negative amount', () => {
    const result = formatCurrencyChf(-42.5)
    expect(result).toBe('− CHF 42.50')
    expect(result.startsWith('-')).toBe(false)
  })
})
