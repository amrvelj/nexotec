import type { TFunction } from 'i18next'

/** A setup-checklist item's words (KAN-214; KAN-805 PR-3). Platform's own items
 * and the contributed ones live under `setup.items.<code>`; a setting of the
 * settings store is an item too, its code the setting's key (`aftersales.time_unit`,
 * the only codes with a dot), named by the setting's own label
 * (`settings.keys.<area>.<name>.label`) and described by one shared sentence. */
export function isSettingItem(code: string): boolean {
  return code.includes('.')
}

export function checklistItemTitle(t: TFunction, code: string): string {
  return isSettingItem(code) ? t(`settings.keys.${code}.label`) : t(`setup.items.${code}.title`)
}

export function checklistItemDescription(t: TFunction, code: string): string {
  return isSettingItem(code) ? t('setup.settingItem.description') : t(`setup.items.${code}.description`)
}
