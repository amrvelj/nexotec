// The frontend mirror of app.core.validators.NormalisedVin (KAN-244,
// KAN-331, KAN-338): every whitespace character removed, upper-cased, then
// ISO 3779 — 17 characters, letters and digits, never I, O or Q. A value
// that fails it is refused by the API (422), so the UI never keeps one as a
// VIN on its own.

const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/

/** The VIN the API would store for `input`, or null when the API would refuse it. */
export function normalisedVin(input: string): string | null {
  const compact = input.replace(/\s/g, '').toUpperCase()
  return VIN_PATTERN.test(compact) ? compact : null
}
