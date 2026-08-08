import { useEffect, useState } from 'react'
import { Group, Select, TextInput } from '@mantine/core'

// Calling-code list scoped to CH + its immediate neighbors/common markets,
// not the full ISO-3166 set — this is a Swiss-market DMS (spec addendum),
// an exhaustive 200-country picker would be scope creep for what's asked.
// Switzerland first and default, per product feedback (2026-08-07).
const COUNTRY_CODES = [
  { code: '+41', label: '🇨🇭 Switzerland (+41)' },
  { code: '+49', label: '🇩🇪 Germany (+49)' },
  { code: '+33', label: '🇫🇷 France (+33)' },
  { code: '+39', label: '🇮🇹 Italy (+39)' },
  { code: '+43', label: '🇦🇹 Austria (+43)' },
  { code: '+423', label: '🇱🇮 Liechtenstein (+423)' },
  { code: '+44', label: '🇬🇧 United Kingdom (+44)' },
  { code: '+1', label: '🇺🇸 United States (+1)' },
] as const

const DEFAULT_COUNTRY_CODE = '+41'

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
  const [countryCode, setCountryCode] = useState(DEFAULT_COUNTRY_CODE)
  const [localNumber, setLocalNumber] = useState('')

  // Re-split whenever the external value changes to something this
  // component didn't itself just produce (e.g. loading an existing
  // customer for edit) — value is the source of truth, not local state.
  useEffect(() => {
    if (!value) {
      setLocalNumber('')
      return
    }
    const split = splitE164(value)
    setCountryCode(split.countryCode)
    setLocalNumber(split.localNumber)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

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
    <Group grow gap="xs" align="flex-end">
      <Select
        label={label}
        data={COUNTRY_CODES.map((c) => ({ value: c.code, label: c.label }))}
        value={countryCode}
        onChange={(next) => {
          const nextCode = next ?? DEFAULT_COUNTRY_CODE
          setCountryCode(nextCode)
          emit(nextCode, localNumber)
        }}
        allowDeselect={false}
        style={{ flex: '0 0 220px' }}
      />
      <TextInput
        label="Number"
        placeholder="791234567"
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
