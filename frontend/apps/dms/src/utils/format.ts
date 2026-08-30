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

// WP-6c PR-12: the frontend mirror of app.core.i18n's format_number_ch/
// format_currency_chf — "identical across all four languages, takes no
// language parameter at all" (that module's own docstring), so unlike
// formatDate/formatDateTime above there is deliberately no `locale`
// parameter here. It matters more than it sounds: Intl.NumberFormat's own
// fr-CH data uses a COMMA decimal separator (verified — `1'234,5` under
// fr-CH vs `1'234.5` under de-CH/it-CH/en-CH), which would silently
// diverge from every PDF WeasyPrint renders (always a period) if this
// forwarded the active UI language instead of pinning to 'de-CH'. The
// grouping separator itself is already the ASCII apostrophe (U+0027) in
// 'de-CH' output, not the typographic U+2019 the backend's own docstring
// warns Python's locale-free approach must guard against — verified
// empirically, no normalization needed on the JS side.

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('de-CH').format(value)
}

export function formatCurrencyChf(value: number): string {
  const rounded = Math.round(value * 100) / 100
  const sign = rounded < 0 ? '− ' : '' // real minus sign U+2212, matching format_currency_chf
  const formatted = new Intl.NumberFormat('de-CH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(rounded))
  return `${sign}CHF ${formatted}`
}
