// The frontend mirror of app.core.validators.NormalisedVin (KAN-244,
// KAN-331, KAN-338): every whitespace character removed, upper-cased, then
// ISO 3779 — 17 characters, letters and digits, never I, O or Q. A value
// that fails it is refused by the API (422), so the UI never keeps one as a
// VIN on its own.

import { ApiError } from '../api/client'

const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/

/** The VIN the API would store for `input`, or null when the API would refuse it. */
export function normalisedVin(input: string): string | null {
  const compact = input.replace(/\s/g, '').toUpperCase()
  return VIN_PATTERN.test(compact) ? compact : null
}

/**
 * KAN-351: 17 letters and digits the API would refuse as a VIN — O typed for
 * 0, say. The configurator's find phase does not keep it as the VIN
 * (`normalisedVin`), so the screen says why instead of dropping it silently.
 * An input with `.`, `/` or `-` is a Werkscode shape
 * (`app/vehicle/services/identification.py`), never flagged.
 */
export function isMalformedVin(input: string): boolean {
  const compact = input.replace(/\s/g, '').toUpperCase()
  return /^[A-Z0-9]{17}$/.test(compact) && !VIN_PATTERN.test(compact)
}

/**
 * KAN-351: the API refused the request body's `vin` (422 from
 * `NormalisedVin`, KAN-331/338). The screen shows the translated rule next
 * to the VIN input instead of a generic save error or the API's English text.
 */
export function isVinRefusal(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 422) return false
  const errors = err.details?.errors
  return (
    Array.isArray(errors) &&
    errors.some((e: unknown) => {
      const loc = (e as { loc?: unknown } | null)?.loc
      return Array.isArray(loc) && loc[0] === 'body' && loc[loc.length - 1] === 'vin'
    })
  )
}
