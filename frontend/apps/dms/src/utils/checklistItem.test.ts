import { describe, expect, it } from 'vitest'
import type { TFunction } from 'i18next'
import { checklistItemDescription, checklistItemTitle, isSettingItem } from './checklistItem'

// KAN-805 PR-3: a setting of the settings store is a checklist item named by its own label.
const t = ((key: string) => `«${key}»`) as unknown as TFunction

describe('checklist item words', () => {
  it('a code with a dot is a setting key', () => {
    expect(isSettingItem('aftersales.time_unit')).toBe(true)
    expect(isSettingItem('bank_account')).toBe(false)
  })

  it('names a setting by its label and a platform item by its title', () => {
    expect(checklistItemTitle(t, 'aftersales.time_unit')).toBe('«settings.keys.aftersales.time_unit.label»')
    expect(checklistItemTitle(t, 'bank_account')).toBe('«setup.items.bank_account.title»')
  })

  it('describes a setting with the one shared sentence', () => {
    expect(checklistItemDescription(t, 'aftersales.time_unit')).toBe('«setup.settingItem.description»')
    expect(checklistItemDescription(t, 'bank_account')).toBe('«setup.items.bank_account.description»')
  })
})
