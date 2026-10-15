// FR-13: "Locale formatting: dates dd.MM.yyyy" — this is a fixed Swiss
// convention shared by all four UI languages, not something that changes
// per language the way translated strings do. The `locale` param still
// takes the active de-CH/fr-CH/it-CH/en-CH tag (not hardcoded 'de-CH') so
// this stays correct if a locale's date convention ever needs to diverge.
export function formatDate(iso: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso))
}

// KAN-180 — a date-only value (`1980-05-01`, what the backend's
// `date.isoformat()` writes into an audit snapshot) names a calendar day,
// not an instant. `new Date('1980-05-01')` parses it as UTC midnight, which
// formatDate would render as the day before anywhere west of Greenwich; the
// day is read back in the zone it was parsed in.
export function formatDateOnly(isoDate: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(isoDate)
  )
}

// KAN-358 — today's calendar day where the user sits, as the ISO date a
// date-only field stores. `new Date().toISOString().slice(0, 10)` is the UTC
// day: between midnight and 01:00 (02:00 in summer) Swiss time it is
// yesterday, so a purchase or cost recorded then would be booked a day early.
export function todayLocalIsoDate(now: Date = new Date()): string {
  const yyyy = String(now.getFullYear()).padStart(4, '0')
  const mm = String(now.getMonth() + 1).padStart(2, '0')
  const dd = String(now.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

export function formatDateTime(iso: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso)
  )
}

// WP-6c PR-12 / KAN-156: formatNumber and formatCurrencyChf, the frontend
// mirror of app.core.i18n's Swiss number formatting. Since KAN-160 their one
// implementation lives in the ui-kit (packages/ui-kit/src/format/
// swissNumber.ts, which explains why they take no locale and pin both
// separators), because the kit's DataGrid needs them too; this module
// re-exports them so every screen keeps importing its formatting from here.
export { formatCurrencyChf, formatNumber } from '@nexotec/ui-kit'
