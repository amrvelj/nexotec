// FR-13: "Locale formatting: dates dd.MM.yyyy" — this is a fixed Swiss
// convention shared by all four UI languages, not something that changes
// per language the way translated strings do. The `locale` param still
// takes the active de-CH/fr-CH/it-CH/en-CH tag (not hardcoded 'de-CH') so
// this stays correct if a locale's date convention ever needs to diverge.
export function formatDate(iso: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso))
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
