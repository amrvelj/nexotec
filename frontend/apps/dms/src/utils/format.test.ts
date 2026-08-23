import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime } from './format'

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
