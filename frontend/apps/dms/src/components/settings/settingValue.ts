import type { TFunction } from 'i18next'
import { formatCurrencyChf, formatNumber } from '../../utils/format'
import type { SettingDeclarationRead, SettingValueBody } from '../../api/types'

/**
 * KAN-805 — one setting's value as the settings screen shows and edits it.
 * The API carries a value in the field of its kind (`switch`, `number` — a
 * number or an amount as a plain decimal string —, `choice`, `text`); an
 * inline editor works on one string, so `toDraft`/`fromDraft` translate
 * between the two. The empty draft means «not set» (for a location: take
 * the Betrieb's value again) — never zero, never «off».
 */

export type SettingKind = 'switch' | 'number' | 'amount' | 'choice' | 'text'

export const SWITCH_ON = 'on'
export const SWITCH_OFF = 'off'

export function settingLabel(t: TFunction, key: string): string {
  return t(`settings.keys.${key}.label`)
}

/** The value in the UI language: a switch's word, a number with its unit, an
 * amount in CHF, a choice's label, a text's translation (else its name). */
export function formatSettingValue(
  t: TFunction,
  language: string,
  declaration: SettingDeclarationRead,
  value: SettingValueBody,
): string {
  switch (declaration.kind as SettingKind) {
    case 'switch':
      return value.switch ? t('settings.value.on') : t('settings.value.off')
    case 'number': {
      const number = formatNumber(Number(value.number ?? 0), declaration.decimals || undefined)
      return declaration.unit ? t('settings.value.withUnit', { number, unit: t(`settings.units.${declaration.unit}`) }) : number
    }
    case 'amount':
      return formatCurrencyChf(Number(value.number ?? 0))
    case 'choice':
      return value.choice ? t(`settings.keys.${declaration.key}.options.${value.choice}`) : ''
    case 'text':
      return value.text ? (value.text.translations?.[language] ?? value.text.name) : ''
    default:
      return ''
  }
}

/** The editor's string for a stored value; '' for «not set». */
export function toDraft(declaration: SettingDeclarationRead, value: SettingValueBody | null | undefined): string {
  if (!value) return ''
  switch (declaration.kind as SettingKind) {
    case 'switch':
      return value.switch ? SWITCH_ON : SWITCH_OFF
    case 'number':
    case 'amount':
      return value.number ?? ''
    case 'choice':
      return value.choice ?? ''
    default:
      return ''
  }
}

/** The request body's value for an editor's string; null clears the slot. */
export function fromDraft(declaration: SettingDeclarationRead, draft: string): SettingValueBody | null {
  const raw = draft.trim()
  if (raw === '') return null
  switch (declaration.kind as SettingKind) {
    case 'switch':
      return { switch: raw === SWITCH_ON }
    case 'number':
    case 'amount':
      // A plain decimal string, as the API takes it: the Swiss grouping and a
      // decimal comma typed by hand are removed, never sent.
      return { number: raw.replace(/['’\s]/g, '').replace(',', '.') }
    case 'choice':
      return { choice: raw }
    default:
      return null
  }
}
