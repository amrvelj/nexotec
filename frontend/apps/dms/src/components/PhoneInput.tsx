import { useMemo, useState } from 'react'
import { Group, Select, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'

// Calling-code list scoped to CH + its immediate neighbors/common markets,
// not the full ISO-3166 set — this is a Swiss-market DMS (spec addendum),
// an exhaustive 200-country picker would be scope creep for what's asked.
// Switzerland first and default, per product feedback (2026-08-07).
const COUNTRY_CODES = [
  { code: '+41', flag: '🇨🇭', nameKey: 'customerDetail.countries.switzerland' },
  { code: '+49', flag: '🇩🇪', nameKey: 'customerDetail.countries.germany' },
  { code: '+33', flag: '🇫🇷', nameKey: 'customerDetail.countries.france' },
  { code: '+39', flag: '🇮🇹', nameKey: 'customerDetail.countries.italy' },
  { code: '+43', flag: '🇦🇹', nameKey: 'customerDetail.countries.austria' },
  { code: '+423', flag: '🇱🇮', nameKey: 'customerDetail.countries.liechtenstein' },
  { code: '+44', flag: '🇬🇧', nameKey: 'customerDetail.countries.unitedKingdom' },
  { code: '+1', flag: '🇺🇸', nameKey: 'customerDetail.countries.unitedStates' },
] as const

const DEFAULT_COUNTRY_CODE = '+41'

// The country select is sized for the longest label it ships — German
// "Vereinigtes Königreich (+44)" — so no country is cut off into what reads
// as a wrong calling code ("Liechtenstein (+42"). KAN-267.
const COUNTRY_SELECT_WIDTH = 272

// Room for a full national number with its trunk 0 (12 digits).
const NUMBER_INPUT_MIN_WIDTH = 150

// The width at which country and number sit side by side. Hosts that lay
// the editor out in a row (RepeatableRowGroup's `valueEditorMinWidth`) pass
// it, so the row gives the editor a line of its own instead of squeezing it;
// narrower than this (a narrow card), the number moves under the country
// (KAN-267).
export const PHONE_INPUT_MIN_WIDTH = COUNTRY_SELECT_WIDTH + 10 + NUMBER_INPUT_MIN_WIDTH

// Longest-prefix match so e.g. +423 (Liechtenstein) isn't mis-split as +41
// followed by a stray leading "3". Re-adds the trunk 0 stripped by emit()
// below, so editing an existing customer shows the number back in the same
// national format a person would recognize and re-type (Swiss "0764808404",
// not the bare E.164 remainder "764808404").
function splitE164(value: string): { countryCode: string; localNumber: string } {
  const sorted = [...COUNTRY_CODES].sort((a, b) => b.code.length - a.code.length)
  for (const { code } of sorted) {
    if (value.startsWith(code)) {
      return { countryCode: code, localNumber: `0${value.slice(code.length)}` }
    }
  }
  return { countryCode: DEFAULT_COUNTRY_CODE, localNumber: value.replace(/^\+/, '') }
}

interface PhoneInputProps {
  label: string
  value: string
  onChange: (value: string) => void
}

export function PhoneInput({ label, value, onChange }: PhoneInputProps) {
  const { t } = useTranslation()
  const [countryCode, setCountryCode] = useState(() => (value ? splitE164(value).countryCode : DEFAULT_COUNTRY_CODE))
  const [localNumber, setLocalNumber] = useState(() => (value ? splitE164(value).localNumber : ''))
  const [splitValue, setSplitValue] = useState(value)
  // A stable list: Mantine's Select re-parses `data` and re-runs its
  // selection-sync effect whenever it gets a new array (every keystroke).
  const countryOptions = useMemo(
    () => COUNTRY_CODES.map((c) => ({ value: c.code, label: `${c.flag} ${t(c.nameKey)} (${c.code})` })),
    [t],
  )

  // Re-split whenever the external value changes (e.g. loading an existing
  // customer for edit, or the value a keystroke just emitted coming back) —
  // value is the source of truth, not local state. Adjusted during render,
  // not in an effect (KAN-167): React re-runs this function before it renders
  // the children, so a keystroke whose emitted value comes back re-split (a
  // digit typed after a space) commits once, where the effect committed it
  // twice.
  if (value !== splitValue) {
    setSplitValue(value)
    if (!value) {
      setLocalNumber('')
    } else {
      const split = splitE164(value)
      setCountryCode(split.countryCode)
      setLocalNumber(split.localNumber)
    }
  }

  const emit = (nextCountryCode: string, nextLocalNumber: string) => {
    // National/trunk format (how people actually type a local number, e.g.
    // Swiss "076 480 84 04") leads with a trunk 0 that must be dropped for
    // E.164 — every country in COUNTRY_CODES uses this convention (and it's
    // a no-op for numbering plans that don't, like the US, since those
    // never start with 0). Concatenating without stripping it silently
    // produces a wrong number that still happens to pass E.164 shape
    // validation (extra digit, not a rejected one).
    const digits = nextLocalNumber.replace(/\D/g, '').replace(/^0+/, '')
    onChange(digits ? `${nextCountryCode}${digits}` : '')
  }

  return (
    // preventGrowOverflow off: by default `grow` caps each child at half the
    // row, which held the country select below its width (KAN-267).
    <Group grow preventGrowOverflow={false} gap="xs" align="flex-end" wrap="wrap">
      <Select
        label={label}
        data={countryOptions}
        value={countryCode}
        onChange={(next) => {
          const nextCode = next ?? DEFAULT_COUNTRY_CODE
          setCountryCode(nextCode)
          emit(nextCode, localNumber)
        }}
        allowDeselect={false}
        style={{ flex: `0 0 ${COUNTRY_SELECT_WIDTH}px` }}
      />
      <TextInput
        label={t('customerDetail.phoneInput.number')}
        placeholder="791234567"
        style={{ flex: `1 1 ${NUMBER_INPUT_MIN_WIDTH}px`, minWidth: NUMBER_INPUT_MIN_WIDTH }}
        value={localNumber}
        onChange={(event) => {
          const next = event.currentTarget.value
          setLocalNumber(next)
          emit(countryCode, next)
        }}
      />
    </Group>
  )
}
