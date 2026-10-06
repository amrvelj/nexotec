import { afterEach, describe, expect, it } from 'vitest'
import i18n from './index'

// KAN-160: counts in translated strings (`{{count, number}}`) go through the
// app's Swiss formatter, not i18next's built-in Intl one — `12'500` under
// every UI language — while `count` stays the raw number plural rules see.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('{{count, number}} interpolation', () => {
  it.each(['de', 'fr', 'it', 'en'])('renders a count as 12\'500 under %s', async (lng) => {
    await i18n.changeLanguage(lng)
    expect(i18n.t('common.showing', { count: 12500 })).toContain("12'500")
    expect(i18n.t('common.showing', { count: 12500 })).not.toMatch(/12[\s ’,.]500/)
  })

  it('still selects the plural form from the raw count', async () => {
    await i18n.changeLanguage('en')
    expect(i18n.t('customerCreate.duplicates.heading', { count: 1 })).toBe('Possible existing customer')
    expect(i18n.t('customerCreate.duplicates.heading', { count: 1200 })).toBe("1'200 possible existing customers")
  })
})
